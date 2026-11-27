use super::model::{CellNumber, Position, Reference};
use crate::Error;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct Placement {
    pub content: bool,
    pub blank: bool,
    pub number: Option<CellNumber>,
    pub length: usize,
    pub pending: bool,
}

impl Placement {
    pub fn reference(self) -> Reference {
        if self.content {
            self.number.map_or(Reference::Empty, Reference::Number)
        } else if self.blank {
            Reference::Blank
        } else {
            Reference::Empty
        }
    }

    fn is_key(self) -> bool {
        self.content || self.blank
    }
}

struct Marker<'a> {
    position: &'a Position,
    raw: Placement,
    old_key: bool,
    has_tween: bool,
    missing: bool,
}

impl<'a> Marker<'a> {
    fn new(position: &'a Position) -> Self {
        let old = position.compatibility.legacy.as_ref();
        let raw = old.and_then(|old| old.legacy.as_ref()).map_or(
            Placement {
                content: matches!(position.reference, Reference::Number(_))
                    || position.compatibility.unnumbered_content,
                blank: position.reference == Reference::Blank,
                number: position.number(),
                length: 1,
                pending: position.pending,
            },
            |legacy| Placement {
                content: legacy.state.has_content,
                blank: legacy.state.is_blank_key,
                number: legacy.number,
                length: legacy.state.exposure,
                pending: legacy.state.pending,
            },
        );
        Self {
            position,
            raw,
            old_key: old.is_some_and(|old| old.key_start),
            has_tween: position.tween.is_some() || position.tween_member.is_some(),
            missing: old.is_some_and(|old| old.legacy.is_none()),
        }
    }
}

fn expanded_matches(rows: &[Placement], references: &[Reference]) -> bool {
    let mut current = Reference::Empty;
    let mut stop = 0;
    for (column, (row, reference)) in rows.iter().zip(references).enumerate() {
        if !row.pending && row.is_key() {
            current = row.reference();
            stop = column.saturating_add(row.length.max(1));
        }
        let actual = if column < stop {
            current
        } else {
            Reference::Empty
        };
        if actual != *reference {
            return false;
        }
    }
    true
}

fn recompress(markers: &[Marker<'_>], references: &[Reference]) -> Result<Vec<Placement>, Error> {
    let mut rows: Vec<_> = markers
        .iter()
        .map(|marker| Placement {
            pending: marker.position.pending,
            ..marker.raw
        })
        .collect();
    let mut column = 0;
    let mut last_key: Option<usize> = None;
    while column < rows.len() {
        let reference = references[column];
        if rows[column].pending {
            if let Some(start) = last_key {
                if reference != Reference::Empty && rows[start].reference() == reference {
                    rows[start].length = column - start + 1;
                }
            }
            column += 1;
            continue;
        }
        let mut end = column + 1;
        if reference != Reference::Empty {
            while end < rows.len() && references[end] == reference {
                let marker = &markers[end];
                if marker.old_key
                    || marker.position.blank_start
                    || marker.position.pending
                    || marker.has_tween
                    || markers[end - 1].has_tween
                {
                    break;
                }
                end += 1;
            }
            last_key = Some(column);
        }
        let row = &mut rows[column];
        row.content = matches!(reference, Reference::Number(_))
            || markers[column].position.compatibility.unnumbered_content;
        row.blank = reference == Reference::Blank;
        row.length = end - column;
        if let Reference::Number(number) = reference {
            row.number = Some(number);
        }
        for held in &mut rows[column + 1..end] {
            held.content = false;
            held.blank = false;
            held.length = 1;
        }
        column = end;
    }
    if !expanded_matches(&rows, references) {
        return Err(Error::Invalid(
            "配置待ちとシート参照が競合しています".into(),
        ));
    }
    Ok(rows)
}

// Python project_v1_state / compress_v1 はシート全体で元入力の採用を決める。
pub(super) fn project(columns: &[Vec<&Position>]) -> Result<Vec<Vec<Placement>>, Error> {
    let markers: Vec<Vec<_>> = columns
        .iter()
        .map(|column| {
            column
                .iter()
                .map(|position| Marker::new(position))
                .collect()
        })
        .collect();
    let references: Vec<Vec<_>> = columns
        .iter()
        .map(|column| {
            column
                .iter()
                .map(|position| position.reference_for(false))
                .collect()
        })
        .collect();
    let original: Vec<Vec<_>> = markers
        .iter()
        .map(|column| column.iter().map(|marker| marker.raw).collect())
        .collect();
    let unchanged =
        markers
            .iter()
            .zip(&original)
            .zip(&references)
            .all(|((markers, original), references)| {
                markers.iter().all(|marker| {
                    !marker.missing
                        && (!marker.position.compatibility.unnumbered_content
                            || marker.position.pending
                            || marker.raw.content)
                        && marker.raw.is_key() == marker.old_key
                        && (marker.raw.blank && !marker.raw.content) == marker.position.blank_start
                        && marker.raw.pending == marker.position.pending
                }) && expanded_matches(original, references)
            });
    if unchanged {
        return Ok(original);
    }
    markers
        .iter()
        .zip(&references)
        .map(|(markers, references)| recompress(markers, references))
        .collect()
}
