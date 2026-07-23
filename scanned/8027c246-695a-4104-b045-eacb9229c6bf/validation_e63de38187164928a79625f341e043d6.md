The complete call chain is now clear. Here is the full analysis:

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` — `sender` Is the Router, Not the Original User - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address passed by the pool. The pool always passes its own `msg.sender` as `sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the original user. The hook therefore checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. Any pool that adds the router to its allowlist (which is required for router-based swaps to function at all) becomes fully open to every user, defeating the allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool::swap`, the pool calls:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
``` [1](#0-0) 

`ExtensionCalling::_beforeSwap` then encodes that `sender` value and forwards it to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — The router is `msg.sender` at the pool level.**

`MetricOmmSimpleRouter::exactInputSingle` (and all other `exact*` functions) calls `pool.swap()` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So when the pool executes, `msg.sender = router`. The `sender` forwarded to `beforeSwap` is the **router address**, not the original user.

**Step 3 — The hook checks the router's identity, not the user's.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [4](#0-3) 

Here `msg.sender = pool` and `sender = router`. The check resolves to `allowedSwapper[pool][router]`. The original user's address is never consulted.

**Step 4 — The bypass.**

For any user to swap through the router on an allowlisted pool, the pool admin must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, **every** user — including those the admin explicitly excluded — can call any `exact*` function on the router and the hook will pass, because the hook only sees the router's address.

The `allowedSwapper` mapping is keyed by `(pool, swapper)`: [5](#0-4) 

There is no mechanism in the hook, the pool, or the router to recover or forward the original `msg.sender` of the router call.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may swap on a pool. Once the router is allowlisted (a prerequisite for any router-based swap to succeed), the allowlist is completely ineffective: any unprivileged address can bypass it by routing through `MetricOmmSimpleRouter`. This breaks the core access-control functionality of the extension and allows unauthorized swaps on pools that were intended to be restricted.

---

### Likelihood Explanation

Any pool that (a) configures `SwapAllowlistExtension` and (b) allows router-based swaps is affected. The router is a standard, publicly deployed periphery contract. No special privileges, malicious setup, or non-standard tokens are required. The attacker only needs to call a public `exact*` function on the router.

---

### Recommendation

The pool must forward the **original initiator** rather than its own `msg.sender`. One approach: add an `initiator` field to `extensionData` that the router populates with `msg.sender` before calling `pool.swap()`, and have the extension read and verify it. A more robust approach is for the pool to accept an explicit `initiator` parameter in `swap()` and pass it as `sender` to hooks, while the router always sets `initiator = msg.sender`. Either way, the hook must be able to verify the true originating address independently of the call stack.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Pool admin calls E.setAllowedToSwap(P, router, true)   // required for router swaps
3. Pool admin calls E.setAllowedToSwap(P, alice, true)    // intended allowlist
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
5. Router calls P.swap(recipient, zeroForOne, amount, ...) — msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[P][router] == true  → passes
8. Bob's swap executes successfully despite not being on the allowlist.
```

Direct swap by Bob (without router) would revert: `allowedSwapper[P][bob] == false`. The router path silently bypasses the check.

---

**Note on the "velocity-envelope" framing in the question:** The question conflates the allowlist bypass with a velocity/price-change cap. There is no per-block price-change cap or squared-envelope check anywhere in `SwapAllowlistExtension`. That part of the question's framing is not supported by the code and is rejected. The real and only finding here is the allowlist identity confusion described above.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
