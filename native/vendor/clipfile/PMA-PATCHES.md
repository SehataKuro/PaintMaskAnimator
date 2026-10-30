# PMA compatibility patch

Source: crates.io `clipfile` 1.2.0 (MIT; see LICENSE).
Registry checksum: 455391a3916d7f93cd0f78bacaa1a96c6a082900cdd334fd53f0d28469d4e7b9.

Only src/, Cargo.toml, README.md and LICENSE are included. Example targets are
omitted from Cargo.toml because their sources are not vendored.

`src/animation.rs`: a decoded image-cel mixer with no FCurves represents an
unassigned animation folder. Retain its track and expose an empty key sequence.
Do not remove its Track row: other tracks can link through it. Nonempty mixers
missing ImageCelName, invalid keys and broken track chains still fail validation.

Reproduced with a CSP 2.1.0 file containing metadata-only mixers. Regression
coverage is in tests/test_clip_animation.py. No user artwork is included here.

Remove this local dependency once upstream supports all compatibility cases
documented here.

`src/animation.rs`: scope Track rows by both BankId and ItemId (timeline ID)
when ItemId is present. CSP can save multiple timelines in one bank, each with
its own FirstTrack/TrackNextIndex chain. Validating all bank rows together
incorrectly reports unreachable tracks and can mix cel assignments. Schemas
without ItemId retain bank-only behavior. Tests select each of two timelines
sharing a bank and the same layer; genuine chain validation remains enabled.

`src/raster.rs`: support the observed CSP two-plane monochrome gray-alpha layout
with the exact packing record
`[17,1,1,1,8192,1,32,1,32,65536,256,256,8,8,1,0]`.
Each 256x256 tile contains 8192 MSB-first alpha bytes followed by 8192 gray
bytes; tile parameters are channel-count 0, reserved 16384, width/height 256.
Validate the record, tile parameters and exact decompressed length; retain
existing rejection for other bit-packed and high-depth formats. The source
file's 8-bit lower mipmaps were used to cross-check plane order and bit order.
Synthetic black, white and transparent pixels cover this layout in the Python
integration tests. Remove this patch when upstream supports this packing.
