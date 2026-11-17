//! ZIP のファイル操作と Python のバッファを結ぶ入口。
use std::fs::File;
use std::io::BufReader;
use std::path::PathBuf;
use std::sync::Mutex;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

type Reader = pma_core::project::zip::ZipReader<BufReader<File>>;

#[pyclass]
pub struct ZipReader {
    reader: Mutex<Option<Reader>>,
}

#[pymethods]
impl ZipReader {
    #[new]
    fn new(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let reader = py.detach(|| {
            let file = File::open(path)?;
            Reader::open(BufReader::new(file))
                .map_err(|error| PyValueError::new_err(error.to_string()))
        })?;
        Ok(Self {
            reader: Mutex::new(Some(reader)),
        })
    }

    fn entries(&self) -> PyResult<Vec<(String, u64, u16)>> {
        let guard = self
            .reader
            .lock()
            .map_err(|_| PyRuntimeError::new_err("ZIP lock"))?;
        let reader = guard
            .as_ref()
            .ok_or_else(|| PyValueError::new_err("closed ZIP"))?;
        Ok(reader
            .entries()
            .iter()
            .map(|e| (e.name.clone(), e.file_size, e.flag_bits))
            .collect())
    }

    fn read<'py>(&self, py: Python<'py>, index: usize) -> PyResult<Bound<'py, PyBytes>> {
        // ロック待ちも GIL の外に置く。出力は PyBytes に直接展開する。
        let size = py.detach(|| {
            let guard = self
                .reader
                .lock()
                .map_err(|_| PyRuntimeError::new_err("ZIP lock"))?;
            let reader = guard
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("closed ZIP"))?;
            let entry = reader
                .entries()
                .get(index)
                .ok_or_else(|| PyValueError::new_err("ZIP index"))?;
            usize::try_from(entry.file_size).map_err(|_| PyValueError::new_err("ZIP size"))
        })?;
        PyBytes::new_with(py, size, |out| {
            py.detach(|| {
                let mut guard = self
                    .reader
                    .lock()
                    .map_err(|_| PyRuntimeError::new_err("ZIP lock"))?;
                let reader = guard
                    .as_mut()
                    .ok_or_else(|| PyValueError::new_err("closed ZIP"))?;
                reader
                    .read_into(index, out)
                    .map_err(|e| PyRuntimeError::new_err(e.to_string()))
            })
        })
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            self.reader
                .lock()
                .map_err(|_| PyRuntimeError::new_err("ZIP lock"))?
                .take();
            Ok(())
        })
    }
}
