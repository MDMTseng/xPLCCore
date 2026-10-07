# QEC-R11MP3S ESI (DMP, 3-axis stepper EtherCAT slave)

`QEC-R11MP3S_rev20230816.xml` is DMP's `QEC-R11MP3S.xml` (revision
`0x20211012`) with only the `RevisionNo` changed to `#x20230816`, the
revision the machine's QEC reports (`0x1018:03`; vendor `0xBC3`, product
`0x86D0D6`, `0x1008` "MI_X...", `0x100A` "5.12..."). DMP publishes no ESI
for that revision (checked 2026-10-07).

Why this body, read from the device over SDO on 2026-10-07:

| Object | Device | 20211012 ESIs | MP3S 0x0134FEC6 (2025-04) | R00MP3S v112 |
|---|---|---|---|---|
| `0x1A00` includes `5024:01` | yes | yes | no | no |
| `0x1A04:05` = `5024:01` (PP TxPDO, never written by CODESYS) | yes | yes | no | no |
| `0x1A40` | absent | -- | present | -- |
| `0x1B00`, `0x502A` | absent | -- | -- | present |

So the firmware has the 20211012 PDO layout (slots Axis 1..3, modules CSP
CSV `#x100/#x110/#x120`, PP `#x104/...`). The R00MP3S v112 ESI is another
product (`0x86D0D9`), not a replacement.

History: until 2026-10-07 the project used `QEC-R11MV3S_TTT.xml`, the
QEC-R11MV3S ESI (`0x86D0E4`) with product / revision / name patched to
`0x86D0D6` / `0x20211012` / `QEC-R11MP3S_V`: same PDOs, MV3S object
dictionary types. The master does not check the revision, so it ran.

Install: `jobs/templates/import_esi.py` with `ESI_PATH` / `MATCH` set to
this file / `0086D0D620230816`.
