# Content signature fixture (MangaPixer v1)

`expected.tsv` holds `<file name>\t<signature>` lines computed by **MangaPixer's own code**, not by MangaList:
`src/MangaPixer.Core/Media/ContentSignature.cs` from the MangaPixer repository at commit `2abae26`, copied
unmodified (`git show 2abae26:src/MangaPixer.Core/Media/ContentSignature.cs`, SHA-256 of the copy
`8763855472e1a5e63e902f127cbe21a3d32d098e00f1295458e08a65bb9e6973`), compiled on 2026-10-03 in a throwaway
`mcr.microsoft.com/dotnet/sdk:10.0` container (SDK 10.0.401, `net10.0`, `ImplicitUsings` on) with this
`Program.cs` next to it:

```csharp
using com.lifepixer.mangapixer.Core.Media;
foreach (var f in Directory.GetFiles(args[0]).OrderBy(x => x, StringComparer.Ordinal))
    Console.WriteLine($"{Path.GetFileName(f)}\t{ContentSignature.TryComputeFile(f)}");
```

The input files are synthetic and generated, never real archives: file `<name>.bin` holds the first
`<size>` bytes of `sha256("<name>:0") + sha256("<name>:1") + ...` (the `SIZES` table and `synthetic_bytes`
in `tests/identity/test_signature.py`). The sizes cover the edges of the layout: empty, 1 byte, < 64 KiB,
64 KiB - 1 / exactly / + 1, between 64 and 128 KiB, 128 KiB - 1 / exactly / + 1, and larger files.

`tests/identity/test_signature.py` regenerates the files and asserts that `mangalist.identity.signature`
gives exactly these strings. Do not edit `expected.tsv` by hand; to refresh it, repeat the steps above and
note the new MangaPixer commit here.
