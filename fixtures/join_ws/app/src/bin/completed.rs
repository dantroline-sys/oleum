//! SPIKE-0 fixture — the fully-formed twin of incomplete.rs, i.e. the state of the
//! buffer *after* a candidate is accepted.  Mechanisms C/D (hover / moniker at the
//! accepted identifier) are measured here.  Must compile: `cargo check --bin completed`
//! is the fixture gate.  `// case:<name> target:<token>` markers drive the harness.
use ext::{Greet, SliceStats, Widget};

fn main() {
    let mut v: Vec<i32> = Vec::new(); // case:assoc_fn target:new
    v.push(1); // case:inherent_method target:push
    let s = String::from("  hi ");
    let t = s.trim(); // case:deref_method target:trim
    let total: i32 = v.iter().map(|x| x + 1).sum(); // case:trait_method_std target:map
    let m = v.mean(); // case:ext_trait_method target:mean
    let w = Widget;
    let l = w.label(); // case:reexport_method target:label
    let g = w.generated(); // case:macro_generated target:generated
    let gr = w.greet(); // case:user_trait_method target:greet
    let (mut a, mut b) = (1, 2);
    std::mem::swap(&mut a, &mut b); // case:free_fn_generic target:swap
    println!("{t} {total} {m} {l} {g} {gr} {a} {b}");
}
