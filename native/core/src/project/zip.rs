//! ファイルシステムに依存しない ZIP の読み書き。
use std::io::{Read, Seek, SeekFrom, Write};

use ::zip::ZipArchive;
pub use ::zip::{result::ZipError, CompressionMethod};

use crate::Error;

fn invalid(error: impl std::fmt::Display) -> Error {
    Error::Archive(error.to_string())
}

/// 中央ディレクトリの項目。名前・宣言サイズ・フラグは zipfile と同じ。
#[derive(Debug, Clone)]
pub struct ZipEntry {
    /// zipfile と一致するファイル名。
    pub name: String,
    /// 中央ディレクトリに宣言された展開サイズ。
    pub file_size: u64,
    /// 中央ディレクトリのフラグ全ビット。
    pub flag_bits: u16,
    /// STORED または DEFLATED。
    pub compression: CompressionMethod,
}

/// STORED/DEFLATED の読み取り専用 ZIP。パスを扱わず Read + Seek を保持する。
pub struct ZipReader<R> {
    archive: ZipArchive<R>,
    entries: Vec<ZipEntry>,
}

impl<R: Read + Seek> ZipReader<R> {
    /// 中央ディレクトリ順の項目を返すリーダーを開く。
    /// 破損、重複名、特殊フラグ、別圧縮、ZIP64 以外の拡張は Archive を返す。
    /// 呼び出し側は開けない場合 zipfile に戻す。UTF-8/CP437、前置データ、
    /// コメント、ZIP64 は対応する。フラグなしでも UTF-8 として有効な非 ASCII 名は
    /// zip クレートと zipfile の解釈が異なるため Archive を返す。エントリー本体の CRC は read_into で検査する。
    pub fn open(reader: R) -> Result<Self, Error> {
        let archive = ZipArchive::new(reader).map_err(invalid)?;
        if archive.has_duplicate_names() {
            return Err(invalid("重複名"));
        }
        let mut entries = Vec::new();
        for index in 0..archive.len() {
            let entry = archive.by_index_data(index).map_err(invalid)?;
            let flags = entry.flags().as_u16();
            if flags & !0x80e != 0
                || !matches!(
                    entry.compression(),
                    CompressionMethod::Stored | CompressionMethod::Deflated
                )
            {
                return Err(invalid("未対応の圧縮またはフラグ"));
            }
            let name = entry.name().map_err(invalid)?.into_owned();
            if name.contains('\0') || name.contains('\\') {
                return Err(invalid("NUL またはバックスラッシュを含む名前"));
            }
            entries.push(ZipEntry {
                name,
                file_size: entry.size(),
                flag_bits: flags,
                compression: entry.compression(),
            });
        }
        // Unicode Path 等による名前補正を採用しない。zipfile はこの拡張を無視する。
        // 元の reader で中央ヘッダーを調べ、unsafe なメタデータ再利用を避けて開き直す。
        let start = archive.central_directory_start();
        let mut reader = archive.into_inner();
        reader.seek(SeekFrom::Start(start)).map_err(invalid)?;
        for entry in &entries {
            let mut header = [0; 46];
            reader.read_exact(&mut header).map_err(invalid)?;
            if &header[..4] != b"PK\x01\x02" {
                return Err(invalid("中央ヘッダー"));
            }
            let word = |i| u16::from_le_bytes([header[i], header[i + 1]]);
            let mut name = vec![0; usize::from(word(28))];
            reader.read_exact(&mut name).map_err(invalid)?;
            if word(8) != entry.flag_bits || name.contains(&0) {
                return Err(invalid("名前またはフラグ"));
            }
            if word(8) & 0x800 != 0 {
                std::str::from_utf8(&name).map_err(invalid)?;
            } else if !name.is_ascii() && std::str::from_utf8(&name).is_ok() {
                // zip 9.6.1 はフラグなしでも UTF-8 を優先するが zipfile は CP437。
                return Err(invalid("フラグなしの UTF-8 名"));
            }
            let mut extra = vec![0; usize::from(word(30))];
            reader.read_exact(&mut extra).map_err(invalid)?;
            let mut remaining = extra.as_slice();
            while !remaining.is_empty() {
                if remaining.len() < 4 {
                    return Err(invalid("拡張ヘッダー"));
                }
                let kind = u16::from_le_bytes([remaining[0], remaining[1]]);
                let size = usize::from(u16::from_le_bytes([remaining[2], remaining[3]]));
                if kind != 1 || size > remaining.len() - 4 {
                    return Err(invalid("未対応の拡張ヘッダー"));
                }
                remaining = &remaining[4 + size..];
            }
            reader
                .seek(SeekFrom::Current(i64::from(word(32))))
                .map_err(invalid)?;
        }
        reader.seek(SeekFrom::Start(0)).map_err(invalid)?;
        let archive = ZipArchive::new(reader).map_err(invalid)?;
        Ok(Self { archive, entries })
    }

    /// zipfile.infolist と同じ中央ディレクトリ順の宣言情報を返す。
    pub fn entries(&self) -> &[ZipEntry] {
        &self.entries
    }

    /// 宣言サイズの out に直接展開する。追加の出力バッファは確保しない。
    /// 無効な番号、長さ不一致、展開失敗、CRC 不一致、宣言サイズ超過は Archive。
    /// 失敗時の out の内容は未規定。末尾検査は最大1バイトに制限する。
    pub fn read_into(&mut self, index: usize, out: &mut [u8]) -> Result<(), Error> {
        let entry = self.entries.get(index).ok_or_else(|| invalid("項目番号"))?;
        if u64::try_from(out.len()).ok() != Some(entry.file_size) {
            return Err(invalid("展開サイズ"));
        }
        let mut file = self.archive.by_index(index).map_err(invalid)?;
        file.read_exact(out).map_err(invalid)?;
        if file.read(&mut [0]).map_err(invalid)? != 0 {
            return Err(invalid("展開サイズ超過"));
        }
        Ok(())
    }
}

/// Python の localtime から渡す DOS 日時。奇数秒は zipfile と同じく切り捨てる。
#[derive(Clone, Copy)]
pub struct ZipTime(pub u16, pub u8, pub u8, pub u8, pub u8, pub u8);

/// zipfile.writestr と同じ順で項目を書く。時刻・ファイル操作は呼び出し側が担当する。
pub struct ZipBuilder<W: Write + Seek> {
    writer: ::zip::ZipWriter<W>,
}

impl<W: Write + Seek> ZipBuilder<W> {
    /// Seek でヘッダーを更新するためデータ記述子を使わない writer を作る。
    pub fn new(writer: W) -> Self {
        Self {
            writer: ::zip::ZipWriter::new(writer),
        }
    }

    /// STORED または DEFLATED で追加する。日時・名前・圧縮・I/O の異常は Err。
    /// 権限は zipfile.writestr の 0600、UTF-8 名のフラグはクレートが設定する。
    pub fn add(
        &mut self,
        name: &str,
        data: &[u8],
        method: CompressionMethod,
        time: ZipTime,
    ) -> ::zip::result::ZipResult<()> {
        let date =
            ::zip::DateTime::from_date_and_time(time.0, time.1, time.2, time.3, time.4, time.5)
                .map_err(|_| ::zip::result::ZipError::InvalidArchive("ZIP datetime".into()))?;
        if !matches!(
            method,
            CompressionMethod::Stored | CompressionMethod::Deflated
        ) {
            return Err(::zip::result::ZipError::UnsupportedArchive(
                "ZIP compression",
            ));
        }
        let options = ::zip::write::SimpleFileOptions::default()
            .compression_method(method)
            .compression_level(if method == CompressionMethod::Deflated {
                Some(7)
            } else {
                None
            })
            .last_modified_time(date)
            .external_attributes(0o600 << 16)
            // DEFLATE の最悪時の増加にも余裕を取り、4 GiB 前に ZIP64 を予約する。
            .large_file(data.len() as u64 >= 0xffff_ffff - 16 * 1024 * 1024);
        self.writer.start_file(name, options)?;
        self.writer.write_all(data)?;
        Ok(())
    }

    /// 最後のヘッダーと中央ディレクトリを書き、元の出力を返す。I/O の異常は Err。
    pub fn finish(self) -> ::zip::result::ZipResult<W> {
        self.writer.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ::zip::write::SimpleFileOptions;
    use std::io::{Cursor, Write};

    #[test]
    fn builder_headers_and_roundtrip() {
        for method in [CompressionMethod::Stored, CompressionMethod::Deflated] {
            let mut builder = ZipBuilder::new(Cursor::new(Vec::new()));
            builder
                .add(
                    "日本語.png",
                    b"hello hello hello",
                    method,
                    ZipTime(2026, 11, 17, 12, 34, 57),
                )
                .unwrap();
            let bytes = builder.finish().unwrap().into_inner();
            let word = |i| u16::from_le_bytes([bytes[i], bytes[i + 1]]);
            assert_eq!(word(6), 0x800); // bit3 は 0
            assert_eq!(word(10), (12 << 11) | (34 << 5) | 28);
            assert_eq!(word(12), (46 << 9) | (11 << 5) | 17);
            assert_eq!(word(28), 0);
            assert_eq!(u32::from_le_bytes(bytes[22..26].try_into().unwrap()), 17);
            assert!(u32::from_le_bytes(bytes[18..22].try_into().unwrap()) > 0);
            let mut reader = ZipReader::open(Cursor::new(bytes)).unwrap();
            let mut out = [0; 17];
            reader.read_into(0, &mut out).unwrap();
            assert_eq!(&out, b"hello hello hello");
        }
        let mut builder = ZipBuilder::new(Cursor::new(Vec::new()));
        assert!(builder
            .add(
                "x",
                b"",
                CompressionMethod::Stored,
                ZipTime(1979, 1, 1, 0, 0, 0)
            )
            .is_err());
    }

    fn sample(method: CompressionMethod) -> Vec<u8> {
        let mut writer = ::zip::ZipWriter::new(Cursor::new(Vec::new()));
        for name in ["second", "日本語"] {
            writer
                .start_file(
                    name,
                    SimpleFileOptions::default().compression_method(method),
                )
                .unwrap();
            writer.write_all(b"hello hello hello").unwrap();
        }
        writer.finish().unwrap().into_inner()
    }

    #[test]
    fn roundtrip_order_flags_and_bounds() {
        for method in [CompressionMethod::Stored, CompressionMethod::Deflated] {
            let mut reader = ZipReader::open(Cursor::new(sample(method))).unwrap();
            assert_eq!(reader.entries()[0].name, "second");
            assert_eq!(reader.entries()[1].name, "日本語");
            assert_eq!(reader.entries()[1].flag_bits & 0x800, 0x800);
            for index in 0..2 {
                let mut out = [0; 17];
                reader.read_into(index, &mut out).unwrap();
                assert_eq!(&out, b"hello hello hello");
            }
            assert!(reader.read_into(2, &mut []).is_err());
            assert!(reader.read_into(0, &mut []).is_err());
        }
    }

    #[test]
    fn prefix_comment_zip64_and_empty() {
        let mut writer = ::zip::ZipWriter::new(Cursor::new(Vec::new()));
        writer.set_comment("末尾コメント").unwrap();
        writer
            .start_file(
                "large",
                SimpleFileOptions::default()
                    .compression_method(CompressionMethod::Stored)
                    .large_file(true),
            )
            .unwrap();
        writer.write_all(b"small").unwrap();
        writer
            .start_file(
                "empty",
                SimpleFileOptions::default().compression_method(CompressionMethod::Stored),
            )
            .unwrap();
        let data = writer.finish().unwrap().into_inner();
        for prefix in [&b""[..], &b"leading garbage"[..]] {
            let mut bytes = prefix.to_vec();
            bytes.extend_from_slice(&data);
            let mut reader = ZipReader::open(Cursor::new(bytes)).unwrap();
            let mut out = [0; 5];
            reader.read_into(0, &mut out).unwrap();
            assert_eq!(&out, b"small");
            reader.read_into(1, &mut []).unwrap();
        }
    }

    #[test]
    fn flags_and_ambiguous_name() {
        let original = sample(CompressionMethod::Stored);
        let central = original
            .windows(4)
            .position(|w| w == b"PK\x01\x02")
            .unwrap();
        let mut descriptor = original.clone();
        descriptor[6..8].copy_from_slice(&8u16.to_le_bytes());
        descriptor[central + 8..central + 10].copy_from_slice(&8u16.to_le_bytes());
        let mut reader = ZipReader::open(Cursor::new(descriptor)).unwrap();
        assert_eq!(reader.entries()[0].flag_bits, 8);
        reader.read_into(0, &mut [0; 17]).unwrap();
        let mut encrypted = original.clone();
        encrypted[central + 8..central + 10].copy_from_slice(&1u16.to_le_bytes());
        assert!(ZipReader::open(Cursor::new(encrypted)).is_err());
        let mut ambiguous = original;
        let second = ambiguous[central + 4..]
            .windows(4)
            .position(|w| w == b"PK\x01\x02")
            .unwrap()
            + central
            + 4;
        ambiguous[second + 8..second + 10].fill(0);
        assert!(ZipReader::open(Cursor::new(ambiguous)).is_err());
    }

    #[test]
    fn cp437_name() {
        let mut data = sample(CompressionMethod::Stored);
        for signature in [b"PK\x03\x04", b"PK\x01\x02"] {
            let at = data.windows(4).position(|w| w == signature).unwrap();
            let name_at = at + if signature[2] == 3 { 30 } else { 46 };
            data[name_at] = 0x82;
        }
        let reader = ZipReader::open(Cursor::new(data)).unwrap();
        assert_eq!(reader.entries()[0].name, "éecond");
    }

    #[test]
    fn corrupt_crc_size_and_archive() {
        for method in [CompressionMethod::Stored, CompressionMethod::Deflated] {
            let original = sample(method);
            let central = original
                .windows(4)
                .position(|w| w == b"PK\x01\x02")
                .unwrap();
            for (offset, value) in [(16, 0u32), (24, 1u32), (24, 18u32)] {
                let mut data = original.clone();
                data[central + offset..central + offset + 4].copy_from_slice(&value.to_le_bytes());
                let mut reader = ZipReader::open(Cursor::new(data)).unwrap();
                let mut out = vec![0; reader.entries()[0].file_size as usize];
                assert!(reader.read_into(0, &mut out).is_err());
            }
        }
        for data in [&b""[..], &b"broken"[..], &b"PK\x03\x04"[..]] {
            assert!(ZipReader::open(Cursor::new(data)).is_err());
        }
    }
}
