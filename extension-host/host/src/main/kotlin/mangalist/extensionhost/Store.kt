package mangalist.extensionhost

import java.nio.file.Path
import java.sql.Connection
import java.sql.DriverManager
import java.sql.PreparedStatement
import java.sql.ResultSet
import java.sql.Types
import kotlin.io.path.createDirectories

/** A manga as the host keeps it (Suwayomi's MangaTable, the columns MangaList and the downloader need). */
data class MangaRow(
    val id: Int,
    val sourceId: Long,
    val url: String,
    val title: String,
    val inLibrary: Boolean = false,
    val initialized: Boolean = false,
    val thumbnailUrl: String? = null,
    val author: String? = null,
    val artist: String? = null,
    val description: String? = null,
    val genre: String? = null,
    val status: Int = 0,
    val realUrl: String? = null,
)

/** A chapter as the host keeps it (Suwayomi's ChapterTable, the columns MangaList and the downloader need). */
data class ChapterRow(
    val id: Int,
    val mangaId: Int,
    val url: String,
    val name: String,
    val chapterNumber: Float,
    val scanlator: String?,
    val realUrl: String?,
    val uploadDate: Long,
    val sourceOrder: Int,
    val downloaded: Boolean = false,
    val pageCount: Int = -1,
)

/** One persisted download queue entry (its progress lives in memory only). */
data class QueueRow(
    val chapterId: Int,
    val state: String,
    val tries: Int,
)

/**
 * The host's own database: one SQLite file (`<data>/host.db`, xerial sqlite-jdbc) holding the manga and chapter ids
 * MangaList keeps in its ledger, and the download queue. Ids are `INTEGER PRIMARY KEY AUTOINCREMENT`: stable across
 * restarts and never reused after a delete (as Suwayomi's H2 identity columns), so an id MangaList stored never names
 * another chapter later.
 *
 * One connection, every call synchronized: the host's load is a few calls a minute.
 */
class Store(
    file: Path,
) : AutoCloseable {
    private val conn: Connection

    init {
        file.toAbsolutePath().parent?.createDirectories()
        Class.forName("org.sqlite.JDBC")
        conn = DriverManager.getConnection("jdbc:sqlite:${file.toAbsolutePath()}")
        conn.createStatement().use { st ->
            st.execute("PRAGMA journal_mode=WAL")
            st.execute("PRAGMA synchronous=NORMAL")
            st.execute("PRAGMA busy_timeout=5000")
        }
        migrate()
    }

    private fun migrate() {
        val version = conn.createStatement().use { st -> st.executeQuery("PRAGMA user_version").use { it.next(); it.getInt(1) } }
        if (version < 1) {
            conn.autoCommit = false
            try {
                conn.createStatement().use { st ->
                    st.execute(
                        """
                        CREATE TABLE manga (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            source_id TEXT NOT NULL,
                            url TEXT NOT NULL,
                            title TEXT NOT NULL,
                            in_library INTEGER NOT NULL DEFAULT 0,
                            initialized INTEGER NOT NULL DEFAULT 0,
                            thumbnail_url TEXT, author TEXT, artist TEXT, description TEXT, genre TEXT,
                            status INTEGER NOT NULL DEFAULT 0,
                            real_url TEXT,
                            UNIQUE (source_id, url)
                        )
                        """.trimIndent(),
                    )
                    st.execute(
                        """
                        CREATE TABLE chapter (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            manga_id INTEGER NOT NULL REFERENCES manga (id),
                            url TEXT NOT NULL,
                            name TEXT NOT NULL,
                            chapter_number REAL NOT NULL DEFAULT -1,
                            scanlator TEXT,
                            real_url TEXT,
                            upload_date INTEGER NOT NULL DEFAULT 0,
                            source_order INTEGER NOT NULL DEFAULT 0,
                            downloaded INTEGER NOT NULL DEFAULT 0,
                            page_count INTEGER NOT NULL DEFAULT -1,
                            UNIQUE (manga_id, url)
                        )
                        """.trimIndent(),
                    )
                    st.execute(
                        """
                        CREATE TABLE download_queue (
                            chapter_id INTEGER PRIMARY KEY,
                            position INTEGER NOT NULL,
                            state TEXT NOT NULL,
                            tries INTEGER NOT NULL DEFAULT 0
                        )
                        """.trimIndent(),
                    )
                    st.execute("PRAGMA user_version = 1")
                }
                conn.commit()
            } catch (e: Throwable) {
                conn.rollback()
                throw e
            } finally {
                conn.autoCommit = true
            }
        }
    }

    @Synchronized
    override fun close() = conn.close()

    @Synchronized
    fun <T> transaction(block: Store.() -> T): T {
        if (!conn.autoCommit) return block()
        conn.autoCommit = false
        try {
            val result = block()
            conn.commit()
            return result
        } catch (e: Throwable) {
            conn.rollback()
            throw e
        } finally {
            conn.autoCommit = true
        }
    }

    // --- manga ------------------------------------------------------------------------------------------------------

    private fun manga(rs: ResultSet) =
        MangaRow(
            id = rs.getInt("id"),
            sourceId = rs.getString("source_id").toLong(),
            url = rs.getString("url"),
            title = rs.getString("title"),
            inLibrary = rs.getInt("in_library") != 0,
            initialized = rs.getInt("initialized") != 0,
            thumbnailUrl = rs.getString("thumbnail_url"),
            author = rs.getString("author"),
            artist = rs.getString("artist"),
            description = rs.getString("description"),
            genre = rs.getString("genre"),
            status = rs.getInt("status"),
            realUrl = rs.getString("real_url"),
        )

    @Synchronized
    fun manga(id: Int): MangaRow? = query("SELECT * FROM manga WHERE id = ?", id) { manga(it) }.firstOrNull()

    @Synchronized
    fun mangaByUrl(
        sourceId: Long,
        url: String,
    ): MangaRow? = query("SELECT * FROM manga WHERE source_id = ? AND url = ?", sourceId.toString(), url) { manga(it) }.firstOrNull()

    /** Insert a manga first seen in a search (its id is made now). */
    @Synchronized
    fun insertManga(m: MangaRow): MangaRow {
        val id =
            insert(
                "INSERT INTO manga (source_id, url, title, thumbnail_url, author, artist, description, genre, status) " +
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                m.sourceId.toString(), m.url, m.title, m.thumbnailUrl, m.author, m.artist, m.description, m.genre, m.status,
            )
        return manga(id)!!
    }

    /** Every column but id / source / url / inLibrary. */
    @Synchronized
    fun updateManga(m: MangaRow) {
        update(
            "UPDATE manga SET title = ?, initialized = ?, thumbnail_url = ?, author = ?, artist = ?, description = ?, " +
                "genre = ?, status = ?, real_url = ? WHERE id = ?",
            m.title, if (m.initialized) 1 else 0, m.thumbnailUrl, m.author, m.artist, m.description, m.genre, m.status,
            m.realUrl, m.id,
        )
    }

    @Synchronized
    fun setInLibrary(
        id: Int,
        inLibrary: Boolean,
    ) = update("UPDATE manga SET in_library = ? WHERE id = ?", if (inLibrary) 1 else 0, id)

    // --- chapters ---------------------------------------------------------------------------------------------------

    private fun chapter(rs: ResultSet) =
        ChapterRow(
            id = rs.getInt("id"),
            mangaId = rs.getInt("manga_id"),
            url = rs.getString("url"),
            name = rs.getString("name"),
            chapterNumber = rs.getFloat("chapter_number"),
            scanlator = rs.getString("scanlator"),
            realUrl = rs.getString("real_url"),
            uploadDate = rs.getLong("upload_date"),
            sourceOrder = rs.getInt("source_order"),
            downloaded = rs.getInt("downloaded") != 0,
            pageCount = rs.getInt("page_count"),
        )

    @Synchronized
    fun chapter(id: Int): ChapterRow? = query("SELECT * FROM chapter WHERE id = ?", id) { chapter(it) }.firstOrNull()

    /** The chapters with these ids that exist, in id order. */
    @Synchronized
    fun chapters(ids: Collection<Int>): List<ChapterRow> {
        if (ids.isEmpty()) return emptyList()
        val marks = ids.joinToString(",") { "?" }
        return query("SELECT * FROM chapter WHERE id IN ($marks) ORDER BY id", *ids.toTypedArray()) { chapter(it) }
    }

    /** A manga's chapters, newest first (source order descending, as Suwayomi lists them). */
    @Synchronized
    fun chaptersOf(mangaId: Int): List<ChapterRow> =
        query("SELECT * FROM chapter WHERE manga_id = ? ORDER BY source_order DESC", mangaId) { chapter(it) }

    @Synchronized
    fun insertChapter(c: ChapterRow): Int =
        insert(
            "INSERT INTO chapter (manga_id, url, name, chapter_number, scanlator, real_url, upload_date, source_order, " +
                "downloaded, page_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            c.mangaId, c.url, c.name, c.chapterNumber, c.scanlator, c.realUrl, c.uploadDate, c.sourceOrder,
            if (c.downloaded) 1 else 0, c.pageCount,
        )

    @Synchronized
    fun updateChapter(c: ChapterRow) {
        update(
            "UPDATE chapter SET name = ?, chapter_number = ?, scanlator = ?, real_url = ?, upload_date = ?, " +
                "source_order = ?, downloaded = ?, page_count = ? WHERE id = ?",
            c.name, c.chapterNumber, c.scanlator, c.realUrl, c.uploadDate, c.sourceOrder, if (c.downloaded) 1 else 0,
            c.pageCount, c.id,
        )
    }

    @Synchronized
    fun setDownloaded(
        id: Int,
        downloaded: Boolean,
        pageCount: Int = -1,
    ) = update("UPDATE chapter SET downloaded = ?, page_count = ? WHERE id = ?", if (downloaded) 1 else 0, pageCount, id)

    @Synchronized
    fun deleteChapters(ids: Collection<Int>) {
        if (ids.isEmpty()) return
        val marks = ids.joinToString(",") { "?" }
        update("DELETE FROM chapter WHERE id IN ($marks)", *ids.toTypedArray())
    }

    // --- the download queue -----------------------------------------------------------------------------------------

    @Synchronized
    fun loadQueue(): List<QueueRow> =
        query("SELECT * FROM download_queue ORDER BY position") {
            QueueRow(it.getInt("chapter_id"), it.getString("state"), it.getInt("tries"))
        }

    /** Replace the persisted queue with [rows] (in order). */
    @Synchronized
    fun saveQueue(rows: List<QueueRow>) =
        transaction {
            update("DELETE FROM download_queue")
            conn.prepareStatement("INSERT INTO download_queue (chapter_id, position, state, tries) VALUES (?, ?, ?, ?)").use { ps ->
                rows.forEachIndexed { i, r ->
                    ps.setInt(1, r.chapterId)
                    ps.setInt(2, i)
                    ps.setString(3, r.state)
                    ps.setInt(4, r.tries)
                    ps.addBatch()
                }
                ps.executeBatch()
            }
        }

    // --- plumbing ---------------------------------------------------------------------------------------------------

    private fun bind(
        ps: PreparedStatement,
        args: Array<out Any?>,
    ) {
        args.forEachIndexed { i, a ->
            val n = i + 1
            when (a) {
                null -> ps.setNull(n, Types.NULL)
                is Int -> ps.setInt(n, a)
                is Long -> ps.setLong(n, a)
                is Float -> ps.setDouble(n, a.toDouble())
                is Double -> ps.setDouble(n, a)
                is Boolean -> ps.setInt(n, if (a) 1 else 0)
                else -> ps.setString(n, a.toString())
            }
        }
    }

    private fun <T> query(
        sql: String,
        vararg args: Any?,
        map: (ResultSet) -> T,
    ): List<T> =
        conn.prepareStatement(sql).use { ps ->
            bind(ps, args)
            ps.executeQuery().use { rs ->
                buildList { while (rs.next()) add(map(rs)) }
            }
        }

    private fun update(
        sql: String,
        vararg args: Any?,
    ): Int =
        conn.prepareStatement(sql).use { ps ->
            bind(ps, args)
            ps.executeUpdate()
        }

    private fun insert(
        sql: String,
        vararg args: Any?,
    ): Int =
        conn.prepareStatement(sql).use { ps ->
            bind(ps, args)
            ps.executeUpdate()
            conn.createStatement().use { st -> st.executeQuery("SELECT last_insert_rowid()").use { it.next(); it.getInt(1) } }
        }
}
