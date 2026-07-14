Looking at the external report, the vulnerability class is **arithmetic semantic mismatch / invariant violation through asymmetric operations** — where a "mint" path uses an inflated price and a "redeem" path uses a fair price, violating the 1:1 backing invariant over time.

I need to find an analog in `clvm_rs` where an attacker-controlled input can exploit an asymmetry to violate a core invariant (determinism, cost accuracy, or correctness).

The most suspicious finding is the use of `rand::rng()` inside `op_add` and `op_subtract` in the slow (bignum) path: [1](#0-0) [2](#0-1) 

Both functions distribute operands randomly across two accumulators (`acc[0]`, `acc[1]`) using `rng.random_range(0..2)`, then compute the final result as `acc[0] + acc[1] + small_acc`.

**Analysis of whether this breaks the determinism invariant:**

For `op_add`: every operand is added to either `acc[0]` or `acc[1]`. Since addition is commutative and associative, `acc[0] + acc[1]` is always the same sum regardless of distribution. Result: **deterministic**.

For `op_subtract`: the first operand is added to a random accumulator; subsequent operands are subtracted from a random accumulator. The final value is still `acc[0] + acc[1] + small_acc`. Tracing all 8 possible distributions for three Buffer arguments `[a, b, c]` confirms the result is always `a - b - c`. Result: **deterministic**.

**Cost analysis:** Cost is computed from input atom sizes (`ARITH_COST_PER_BYTE * len(arg)`) and output atom size (`MALLOC_COST_PER_BYTE * len(result)`). Since the result value is always identical regardless of RNG state, the output atom size is identical, and the total cost is **deterministic**. [3](#0-2) 

**Softfork guard:** The guard enforces exact cost equality (`current_cost != guard.expected_cost`), so any non-determinism in cost would cause `SoftforkCostMismatch`. But since cost is deterministic, this path is not triggered. [4](#0-3) 

**Conclusion:** The two-accumulator randomization is a performance optimization to avoid growing one large intermediate `Number` value. Because the final result is `acc[0] + acc[1] + small_acc` and addition is commutative, the mathematical output and the cost are identical across all nodes regardless of RNG state. No invariant (determinism, cost accuracy, or correctness) is violated. No asymmetric operation exists that an att

### Citations

**File:** src/more_ops.rs (L155-158)
```rust
fn malloc_cost(a: &Allocator, cost: Cost, ptr: NodePtr) -> Reduction {
    let c = a.atom_len(ptr) as Cost * MALLOC_COST_PER_BYTE;
    Reduction(cost + c, ptr)
}
```

**File:** src/more_ops.rs (L452-481)
```rust
    // Slow path: fall back to bignum arithmetic
    let mut rng = rand::rng();
    let mut acc = [Number::from(0), Number::from(0)];
    let mut small_acc: Number = 0.into();
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        cost += ARITH_COST_PER_ARG;

        match a.node(arg) {
            NodeVisitor::Buffer(buf) => {
                cost += ARITH_COST_PER_BYTE * (buf.len() as Cost);
                check_cost(cost, max_cost)?;
                let val = number_from_u8(buf);
                acc[rng.random_range(0..2)] += val;
            }
            NodeVisitor::U32(val) => {
                cost += len_for_value(val) as Cost * ARITH_COST_PER_BYTE;
                check_cost(cost, max_cost)?;
                small_acc += val;
            }
            NodeVisitor::Pair(_, _) => {
                Err(EvalErr::InvalidOpArg(
                    arg,
                    "Requires Int Argument: +".to_string(),
                ))?;
            }
        }
    }
    let total = a.new_number(&acc[0] + &acc[1] + small_acc)?;
    Ok(malloc_cost(a, cost, total))
```

**File:** src/more_ops.rs (L532-583)
```rust
    // Slow path: fall back to bignum arithmetic
    let mut rng = rand::rng();
    let mut acc = [Number::from(0), Number::from(0)];
    let mut small_acc: Number = 0.into();
    let mut is_first = true;
    while let Some((arg, rest)) = a.next(input) {
        input = rest;
        cost += ARITH_COST_PER_ARG;
        check_cost(cost, max_cost)?;
        if is_first {
            match a.node(arg) {
                NodeVisitor::Buffer(buf) => {
                    cost += buf.len() as Cost * ARITH_COST_PER_BYTE;
                    check_cost(cost, max_cost)?;
                    acc[rng.random_range(0..2)] += number_from_u8(buf);
                }
                NodeVisitor::U32(val) => {
                    cost += len_for_value(val) as Cost * ARITH_COST_PER_BYTE;
                    check_cost(cost, max_cost)?;
                    small_acc += val;
                }
                NodeVisitor::Pair(_, _) => {
                    return Err(EvalErr::InvalidOpArg(
                        arg,
                        "Requires Int Argument: -".to_string(),
                    ));
                }
            }
        } else {
            match a.node(arg) {
                NodeVisitor::Buffer(buf) => {
                    cost += buf.len() as Cost * ARITH_COST_PER_BYTE;
                    check_cost(cost, max_cost)?;
                    acc[rng.random_range(0..2)] -= number_from_u8(buf);
                }
                NodeVisitor::U32(val) => {
                    cost += len_for_value(val) as Cost * ARITH_COST_PER_BYTE;
                    check_cost(cost, max_cost)?;
                    small_acc -= val;
                }
                NodeVisitor::Pair(_, _) => {
                    return Err(EvalErr::InvalidOpArg(
                        arg,
                        "Requires Int Argument: -".to_string(),
                    ));
                }
            }
        }
        is_first = false;
    }
    let total = a.new_number(&acc[0] + &acc[1] + small_acc)?;
    Ok(malloc_cost(a, cost, total))
```

**File:** src/run_program.rs (L453-469)
```rust
    fn exit_guard(&mut self, current_cost: Cost) -> Result<Cost> {
        // this is called when we are done executing a softfork program.
        // This is when we have to validate the cost
        let guard = self
            .softfork_stack
            .pop()
            .expect("internal error. exiting a softfork that's already been popped");

        if current_cost != guard.expected_cost {
            #[cfg(test)]
            println!(
                "actual cost: {} specified cost: {}",
                current_cost - guard.start_cost,
                guard.expected_cost - guard.start_cost
            );
            return Err(EvalErr::SoftforkCostMismatch);
        }
```
