//! SPIKE-0 fixture crate: a local stand-in for a third-party dependency, so the
//! spike exercises non-std join cases without touching the network.

pub mod inner {
    pub struct Widget;

    impl Widget {
        pub fn label(&self) -> String {
            "widget".into()
        }
    }
}

// Re-export: does the join key see `ext::Widget` or the defining path `ext::inner::Widget`?
pub use inner::Widget;

/// Extension trait on a std type — the itertools pattern.
pub trait SliceStats {
    fn mean(&self) -> f64;
}

impl SliceStats for Vec<i32> {
    fn mean(&self) -> f64 {
        if self.is_empty() {
            0.0
        } else {
            self.iter().sum::<i32>() as f64 / self.len() as f64
        }
    }
}

/// Plain user trait implemented for a user type.
pub trait Greet {
    fn greet(&self) -> String;
}

impl Greet for inner::Widget {
    fn greet(&self) -> String {
        "hello".into()
    }
}

// Macro-generated inherent method: does the canonical path survive expansion?
macro_rules! gen_method {
    ($name:ident, $val:expr) => {
        pub fn $name(&self) -> i32 {
            $val
        }
    };
}

impl inner::Widget {
    gen_method!(generated, 42);
}
