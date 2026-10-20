use std::collections::HashMap;

#[derive(Debug, Clone)]
struct Item<'a> { name: &'a str, qty: u32 }

fn main() {
    let mut stock: HashMap<&str, u32> = HashMap::new();
    let items = vec![Item { name: "蘋果", qty: 3 }, Item { name: "香蕉", qty: 12 }];
    for it in &items {
        *stock.entry(it.name).or_insert(0) += it.qty;
    }
    println!("{:?} total={}", stock, items.iter().map(|i| i.qty).sum::<u32>());
    let c = 'x'; // char literal
}
