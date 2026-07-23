### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any user to bypass per-user swap restrictions when the router is allowlisted — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the per-user restriction by routing through the router.

---

### Finding Description

The pool's `swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][directCaller]`: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `sender` = router address. The extension has no visibility into which end user initiated the router call. The `extensionData` bytes parameter is present in the signature but is unnamed and entirely ignored by the extension — there is no fallback path that recovers the original user identity.

This creates an all-or-nothing split:

| Admin configuration | Direct pool call | Router-mediated call |
|---|---|---|
| Router NOT allowlisted | Allowlisted users pass | **Everyone blocked**, including allowlisted users |
| Router allowlisted | Allowlisted users pass | **Everyone passes**, including non-allowlisted users |

There is no configuration that allows specific users to swap through the router while blocking others. Allowlisting the router is a single binary gate that opens the pool to all users.

The `generate_scanned_questions.py` audit pivot for this exact path states: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific market makers or KYC-verified addresses is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool's LP liquidity, extracting value at oracle-anchored prices. Because the pool is oracle-priced, a non-allowlisted user can execute large swaps that drain one side of the pool's bins, causing direct loss of LP principal. The allowlist guard — the only mechanism protecting LP funds from unauthorized swap flow — is rendered ineffective for all router-mediated paths.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router, which is a routine and expected operational step: without it, even allowlisted users cannot use the router. Any user who observes that the router is allowlisted (readable on-chain via `allowedSwapper[pool][router]`) can immediately exploit the bypass with no further preconditions. The trigger is fully unprivileged.

---

### Recommendation

The extension must recover the original end-user identity rather than relying on `sender` (the direct pool caller). Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Have `MetricOmmSimpleRouter` ABI-encode the original `msg.sender` into `extensionData` before calling `pool.swap()`. Update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router address.

2. **Check both `sender` and decoded origin**: If `sender` is a registered router, decode the real user from `extensionData` and apply the allowlist check against that address instead.

Either approach must be paired with a registry of trusted routers so the extension cannot be tricked by a malicious contract that fabricates a user address in `extensionData`.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin calls setAllowedToSwap(pool, mm1, true)       // allowlist market maker
  admin calls setAllowedToSwap(pool, router, true)    // allow router-mediated swaps for mm1

Attack:
  attacker (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle(pool, ...)

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, ...)          // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ PASSES
      → swap executes, LP funds transferred to attacker

Result:
  Non-allowlisted attacker completes swap.
  allowedSwapper[pool][attacker] == false was never checked.
  The per-user allowlist is fully bypassed.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
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

**File:** generate_scanned_questions.py (L657-663)
```python
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
