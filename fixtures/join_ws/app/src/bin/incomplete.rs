//! SPIKE-0 fixture — truncated completion sites, i.e. the buffer *as the user types*.
//! Deliberately does not compile; rust-analyzer serves completions on broken code by
//! design.  `/*caret:<case>*/` marks the exact completion position (caret sits at the
//! start of the marker).  Mechanisms A/B (completion list / completionItem-resolve)
//! are measured here.
#![allow(unused)]
use ext::{Greet, SliceStats, Widget};

fn main() {
    let mut v: Vec<i32> = Vec::ne/*caret:assoc_fn*/;
    v.pu/*caret:inherent_method*/;
    let s = String::from("  hi ");
    let t = s.tri/*caret:deref_method*/;
    let it = v.iter().ma/*caret:trait_method_std*/;
    let m = v.mea/*caret:ext_trait_method*/;
    let w = Widget;
    let l = w.lab/*caret:reexport_method*/;
    let g = w.gener/*caret:macro_generated*/;
    let gr = w.gre/*caret:user_trait_method*/;
    std::mem::sw/*caret:free_fn_generic*/;
}
