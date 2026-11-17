//! コアの処理で返すエラー。

/// Qt と一致する範囲外、不正なデータ、画像の上限超過を表す。
#[derive(Debug)]
pub enum Error {
    /// 16bit・APNG など、Qt 側で読む形式。
    Unsupported(String),
    /// 壊れたデータや確保できない画像。
    Invalid(String),
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
