### Title
SwapAllowlistExtension gates the router address instead of the actual user when swaps route through MetricOmmSimpleRouter, enabling full allowlist bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is always `msg.sender` of the pool's `swap` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for their allowlisted users — a natural and expected configuration step — any unprivileged user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension dispatcher.**

`MetricOmmPool.swap` always passes `msg.sender` (the direct caller of `swap`) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that `sender` verbatim to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When the user goes through the router, `sender` = router address.

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` in the pool.** [4](#0-3) 

The effective allowlist check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The bypass:** A pool admin who wants allowlisted users to be able to use the router will naturally call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every swap routed through it passes the check — regardless of who the actual user is — because the extension sees only the router's address as `sender`.

---

### Impact Explanation

The swap allowlist is the pool admin's mechanism to restrict which addresses may trade against the pool. Bypassing it allows any unprivileged user to execute swaps the admin intended to block. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, or pools with adversarial-bot exclusions), unauthorized swaps can extract value from LPs or disrupt the pool's intended operation. This is a direct admin-boundary break: an unprivileged actor circumvents a pool-admin-configured access control through a public periphery contract.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router address. This is the natural and expected action for any admin who wants their allowlisted users to be able to use the router — there is no other mechanism to enable router-mediated swaps for allowlisted users while keeping the allowlist active. The contest's own research guidance explicitly flags this path: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

---

### Recommendation

1. **Short term:** Document explicitly that `setAllowedToSwap(pool, router, true)` grants swap access to *all* users, not just those individually allowlisted. Admins must allowlist individual user addresses, not the router.
2. **Long term:** Redesign the extension to accept the actual user's address via `extensionData` (encoded by the router at call time) and verify it against the allowlist, falling back to `sender` for direct pool calls. This mirrors how the `DepositAllowlistExtension` correctly gates `owner` rather than `msg.sender` of the adder.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin allowlists alice:  setAllowedToSwap(pool, alice, true)
3. Admin allowlists router so alice can use it:
       setAllowedToSwap(pool, router, true)
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender in pool = router.
6. Pool calls extension.beforeSwap(router, ...) — sender = router.
7. Extension evaluates: allowedSwapper[pool

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

**File:** generate_scanned_questions.py (L659-663)
```python
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
