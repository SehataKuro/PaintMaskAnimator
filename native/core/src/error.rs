//! コアの処理で返すエラー。

/// Qt と一致する範囲外、不正なデータ、画像の上限超過を表す。
#[derive(Debug)]
pub enum Error {
    /// 16bit・APNG など、Qt 側で読む形式。
    Unsupported(String),
    /// 壊れたデータや確保できない画像。
    Invalid(String),
    /// ZIP の破損または zipfile に戻す形式。
    Archive(String),
    /// RGBA 画像の寸法・矩形・バッファの不正、または画素バッファの確保失敗。
    Image(&'static str),
    /// RGBA 画像の世代番号を使い切った。
    ImageGenerationExhausted,
    /// タイムラインの出力寸法がバッファの上限を超えた。
    TimelineSizeOverflow,
    /// RGBA8888 の画素数が指定のバイト上限を超えた。
    TooLarge {
        width: u32,
        height: u32,
        max_bytes: u64,
    },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Archive(message) => write!(f, "不正な ZIP: {message}"),
            Self::Image(message) => write!(f, "RGBA 画像: {message}"),
            Self::ImageGenerationExhausted => write!(f, "RGBA 画像の世代番号を使い切りました"),
            Self::TimelineSizeOverflow => write!(f, "capacity overflow"),
            Self::Unsupported(message) => write!(f, "未対応の PNG: {message}"),
            Self::Invalid(message) => write!(f, "不正な PNG: {message}"),
            Self::TooLarge {
                width,
                height,
                max_bytes,
            } => write!(
                f,
                "PNG {width}×{height} は上限 {max_bytes} バイトを超えます"
            ),
        }
    }
}

impl std::error::Error for Error {}
