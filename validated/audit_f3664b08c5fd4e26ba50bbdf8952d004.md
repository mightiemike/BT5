### Title
SwapAllowlistExtension gates on the router's address instead of the actual user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` delivered to the extension is the router address — not the actual user. If the pool admin allowlists the router (the natural operational step to support router-mediated swaps on a curated pool), every user, including non-allowlisted ones, bypasses the guard entirely.

---

### Finding Description

**Call chain when a user swaps through the router:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]   // ← wrong actor
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- this is the router, not the user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router address
)
```

`SwapAllowlistExtension.beforeSwap` then checks the router address against the allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). The check resolves to `allowedSwapper[pool][router]`.

A pool admin who wants to support router-mediated swaps on a curated pool must allowlist the router. Once the router is allowlisted, any user — regardless of whether they are individually allowlisted — can call `router.exactInputSingle()` and the extension passes, because the router is the checked identity.

---

### Impact Explanation

**Direct loss of curation policy / unauthorized swap execution on curated pools.**

A pool with `SwapAllowlistExtension` is explicitly designed to restrict who may trade. If the router is allowlisted (the only way to let legitimate users swap through the supported periphery path), the allowlist becomes a no-op for all router-mediated swaps. Any user can trade on a pool that was intended to be restricted, extracting value from LPs who deposited under the assumption that only vetted counterparties could swap against them. This is a broken core pool invariant: the configured guard does not enforce the intended policy on the supported public entrypoint.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a curated pool and wants legitimate allowlisted users to be able to use the router must allowlist the router address. This is the expected operational path, not an edge case. The bypass is therefore reachable by any user on any curated pool that supports router access, which is the common deployment pattern.

---

### Recommendation

The extension must check the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router.** The router already knows `msg.sender` (the real user) at entry. It should forward this as a separate field in `extensionData` (or as a dedicated parameter if the interface is extended), and the extension should decode and check that value.

2. **Alternatively, check `sender` only when `sender` is an EOA or a known non-router contract.** This is fragile; option 1 is preferred.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the LP position owner, which the caller explicitly sets and which is the economically relevant actor). The swap allowlist should adopt an equivalent pattern — either by having the router forward the real initiator, or by requiring the pool to expose the original caller through a dedicated mechanism.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so legitimate users can swap
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds — allowlist bypassed
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
