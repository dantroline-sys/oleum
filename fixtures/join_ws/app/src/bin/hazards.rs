//! Pack fixture — exercises every curated op in producers/op_hazards.toml.
//! tests/hazard_pack_test.py golden-checks that the extractor derives exactly the
//! curated ids from this file; drift between the map and opkey.py fails there.
//! Must compile: `cargo check --bin hazards`.
use std::cell::RefCell;
use std::sync::Mutex;

fn main() {
    let o: Option<i32> = Some(1);
    let _ = o.unwrap();
    let o2: Option<i32> = Some(2);
    let _ = o2.expect("two");
    let r: Result<i32, ()> = Ok(1);
    let _ = r.unwrap();
    let r2: Result<i32, ()> = Ok(2);
    let _ = r2.expect("two");
    std::mem::forget(String::new());
    let t: u32 = unsafe { std::mem::transmute(1i32) };
    let c = RefCell::new(1);
    let _ = *c.borrow();
    *c.borrow_mut() += 1;
    let m = Mutex::new(1);
    let _guard = m.lock();
    let mut v = vec![1, 2];
    let _ = v.remove(0);
    println!("{t}");
    std::process::exit(0);
}
