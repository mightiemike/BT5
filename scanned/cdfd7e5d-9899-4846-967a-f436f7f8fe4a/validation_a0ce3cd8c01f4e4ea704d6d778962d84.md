The vulnerability is real. Here is the precise trace:

**Root cause in `beforeSwap`:** `s.lastMidPriceX64` is written at line 57 **before** the velocity check at line 72. This means every subsequent swap within the same block compares against the **prior sub-swap's mid**, not the block-start mid. [1](#0-0) 

**`blockDiff` collapses to zero for all sub-swaps after the first:** After swap 1 writes `s.lastUpdateBlock = block.number`, every subsequent swap in the same block computes `blockDiff = block.number - block.number = 0`, so `allowedSq = maxChange² * 1`. Each sub-swap is independently bounded by M, but the cumulative move is N×M. [2](#0-1) 

**`multicall` enables this in one transaction:** `multicall` uses `delegatecall` to call `exactInputSingle` N times. Each delegatecall reaches `pool.swap()` → `extension.beforeSwap()` via normal external calls, so the extension's storage (`priceVelocityState`) is mutated after each hop and the rolling baseline advances. [3](#0-2) 

---

### Title
PriceVelocityGuardExtension rolling-baseline bypass via multicall batching allows N×M cumulative same-block price move — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary
`PriceVelocityGuardExtension.beforeSwap` updates `lastMidPriceX64` to the current mid **before** performing the velocity check. When `MetricOmmSimpleRouter.multicall` batches N `exactInputSingle` calls in one transaction, each sub-swap is checked only against the previous sub-swap's mid, not the block-start mid. N swaps each moving the price by M−ε all pass individually while the total cumulative move reaches N×M, defeating the guard's per-block cap.

### Finding Description
In `beforeSwap`:

```solidity
// PriceVelocityGuardExtension.sol lines 54-70
uint128 prevMid = s.lastMidPriceX64;
uint64  prevBlock = s.lastUpdateBlock;

s.lastMidPriceX64 = midPrice;          // ← baseline advanced BEFORE check
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {
    uint256 blockDiff = block.number - prevBlock;   // = 0 for swaps 2..N
    uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
    if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
}
```

For swap 1 (cross-block): `blockDiff ≥ 1`, allowed move = M×√(1+blockDiff).  
For swaps 2…N (same block): `blockDiff = 0`, allowed move = M — but the baseline is now the **previous sub-swap's mid**, not the block-start mid.

`multicall` issues N `delegatecall`s to `exactInputSingle`, each of which calls `pool.swap()` → `extension.beforeSwap()`. Because the extension is an external contract, its storage is mutated normally after each hop. The rolling baseline advances with every sub-swap.

### Impact Explanation
The velocity guard exists to bound how far the oracle-provided mid price can be moved in a single block, protecting LPs from price manipulation. Bypassing it allows an attacker to move the pool price by N×M in one block. LPs are then forced to trade at a price that is N times further from fair value than the guard was designed to permit, resulting in direct loss of LP principal above the intended threshold.

### Likelihood Explanation
Any unprivileged user can call `multicall` with an arbitrary array of `exactInputSingle` calls. No special role, factory ownership, or malicious pool setup is required. The attacker only needs to supply the input tokens for each sub-swap, which they recover (minus fees) from the price movement they induce.

### Recommendation
Snapshot `lastMidPriceX64` at the **start of the block** and hold it fixed for all swaps within that block. Concretely: only update `lastMidPriceX64` when `block.number > lastUpdateBlock`; within the same block, keep `prevMid` as the block-start mid and accumulate the total deviation against that fixed baseline.

```solidity
if (block.number > prevBlock) {
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
} // else: do NOT advance the baseline mid-block
```

The velocity check should then always compare `midPrice` against the block-start `prevMid`.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_velocityGuardBypassViaMulticall() external {
    // Pool configured with PriceVelocityGuardExtension, maxChangePerBlockE18 = M
    // lastMidPriceX64 = P (set in a prior block)

    bytes[] memory calls = new bytes[](3);
    // Each exactInputSingle moves price by M - epsilon
    calls[0] = abi.encodeCall(router.exactInputSingle, (paramsMovingByMEpsilon));
    calls[1] = abi.encodeCall(router.exactInputSingle, (paramsMovingByMEpsilon));
    calls[2] = abi.encodeCall(router.exactInputSingle, (paramsMovingByMEpsilon));

    // All three pass — no PriceVelocityExceeded revert
    router.multicall(calls);

    // Assert cumulative token1 outflow > what a single M-bounded swap would permit
    assertGt(token1BalanceDelta, singleSwapMaxOutflow);
}
```

Each of the three `beforeSwap` calls sees `blockDiff = 0` (after the first) and compares only against the immediately preceding sub-swap's mid. All three pass. The cumulative price move is ≈3M, draining LP token1 balance by the excess beyond the single-swap M cap.

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L54-70)
```text
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
