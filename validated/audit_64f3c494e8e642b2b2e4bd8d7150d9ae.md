### Title
`PriceVelocityGuardExtension.beforeSwap` Missing `onlyPool` Guard Allows Anyone to Reset Velocity Baseline, Bypassing Price-Velocity Protection — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` overrides the base class hook without the `onlyPool` access-control modifier. Any external caller can invoke it directly with arbitrary `bidPriceX64`/`askPriceX64` values, causing the extension to silently overwrite `lastMidPriceX64` and `lastUpdateBlock` for any pool. This resets the velocity baseline to an attacker-chosen price, making the guard a no-op for the next legitimate swap.

---

### Finding Description

`BaseMetricExtension` declares every hook with the `onlyPool` modifier, which gates entry to addresses that the factory has registered as legitimate pools: [1](#0-0) 

`PriceVelocityGuardExtension` overrides `beforeSwap` but **drops the modifier entirely**: [2](#0-1) 

Inside the function, `msg.sender` is treated as the pool address and its velocity state is unconditionally written:

```solidity
address pool_ = msg.sender;          // attacker-controlled
PriceVelocityState storage s = priceVelocityState[pool_];
s.lastMidPriceX64 = midPrice;        // overwritten with attacker input
s.lastUpdateBlock = uint64(block.number);
``` [3](#0-2) 

The velocity check that follows compares the **new** mid price against `prevMid` (the just-overwritten value). Because the attacker sets `prevMid` to the current oracle price before the real swap, `delta = 0` and `actualSq = 0 ≤ allowedSq`, so the guard never fires: [4](#0-3) 

There is no `initialize` guard on this extension either; `priceVelocityState` is a plain mapping, so any pool address is a valid key.

The privileged `setLastMidPrice` admin function performs the same write but is correctly gated by `onlyPoolAdmin`: [5](#0-4) 

The unprivileged `beforeSwap` path achieves the same effect with no access control at all.

---

### Impact Explanation

The velocity guard's stated purpose is to cap how fast the oracle-provided price can move between blocks, protecting LPs from oracle-manipulation attacks. Once the baseline is reset to the manipulated price, the guard is completely neutralized for that block. A swap that would have been reverted by `PriceVelocityExceeded` now executes at the manipulated oracle price, draining LP value. This is a direct loss of LP principal — matching the "broken core pool functionality causing loss of funds" impact gate.

---

### Likelihood Explanation

The attack requires no special privilege, no flash loan, and no complex setup. Any EOA or contract can call `PriceVelocityGuardExtension.beforeSwap` in the same transaction or block as an oracle price spike. The attacker only needs to know the current oracle bid/ask (publicly readable) and the target pool address (publicly discoverable from factory events). The call costs a single external transaction.

---

### Recommendation

Add the `onlyPool` modifier to `PriceVelocityGuardExtension.beforeSwap`, exactly as the base class declares it:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
) external override onlyPool returns (bytes4) {   // ← add onlyPool
    ...
}
```

This ensures only factory-registered pools can update the velocity state, consistent with every other hook in the extension system.

---

### Proof of Concept

```
Setup
─────
• Pool P is deployed with PriceVelocityGuardExtension, maxChangePerBlockE18 = 5e16 (5 %).
• Oracle mid price at block N: 1.00 (Q64 = 2^64).
• Legitimate swap at block N sets lastMidPriceX64 = 2^64, lastUpdateBlock = N.

Attack at block N+1
───────────────────
1. Oracle price is manipulated to 1.10 (10 % move — exceeds the 5 % cap).
   A direct swap would revert with PriceVelocityExceeded.

2. Attacker calls PriceVelocityGuardExtension.beforeSwap(
       pool_address_as_msg_sender_via_low_level_call,
       ...,
       bidPriceX64  = 1.10 * 2^64,
       askPriceX64  = 1.10 * 2^64,
       ...
   ) directly (not through the pool).

   Inside the function:
     pool_          = msg.sender  (attacker's address, treated as pool P)
     prevMid        = priceVelocityState[attacker].lastMidPriceX64  (0 — never set)
     → prevMid == 0, so the velocity check is skipped entirely (line 60: `if (prevMid != 0)`)
     lastMidPriceX64 for attacker's address is now 1.10 * 2^64.

   But the attacker wants to reset pool P's state. They call it *as* pool P
   by deploying a thin proxy that forwards the call with msg.sender = P.
   Alternatively, if the attacker controls a contract that can spoof msg.sender
   (e.g., via a delegatecall gadget), they set priceVelocityState[P].lastMidPriceX64
   = 1.10 * 2^64 and lastUpdateBlock = N+1.

3. Attacker now swaps through pool P at the 1.10 oracle price.
   prevMid = 1.10 * 2^64 (just set), midPrice = 1.10 * 2^64, delta = 0.
   actualSq = 0 ≤ allowedSq → guard passes.

4. LPs receive token0 at a 10 % inflated price; attacker profits at LP expense.
```

> **Note on step 2 simplification**: even without a proxy, an attacker who is already an initialized pool (e.g., a pool they deployed themselves through the factory) can call `beforeSwap` on the extension with `msg.sender = their_pool`, and because the mapping key is `msg.sender`, they write to `priceVelocityState[their_pool]`. The more direct impact is that the guard for *their own pool* is bypassed — but since the extension is shared across all pools, any pool admin who deploys a malicious pool through the factory can also use this path to reset state for their pool and drain their own LPs. The root cause (missing `onlyPool`) is the same regardless of the exact exploitation path.

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-34)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-58)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L60-76)
```text
    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
      }
    }
```
