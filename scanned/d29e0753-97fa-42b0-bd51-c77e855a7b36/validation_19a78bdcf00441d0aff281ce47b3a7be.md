### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to let allowlisted users use the standard periphery path), every non-allowlisted user can bypass the gate by routing through the same router.

---

### Finding Description

**Actor binding mismatch in `SwapAllowlistExtension`**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- this is the router when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When the user goes through `MetricOmmSimpleRouter.exactInputSingle()`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  ...
  params.extensionData
);
```

The pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Consequence**: The pool admin faces an impossible choice:
1. **Allowlist the router** → every user (including non-allowlisted ones) can bypass the gate by calling `router.exactInputSingle()`, because the extension sees `sender = router`.
2. **Do not allowlist the router** → allowlisted users cannot use the standard periphery path at all; they must call `pool.swap()` directly.

Either way the allowlist invariant is broken for the router-mediated path.

---

### Impact Explanation

A curated pool (e.g., institutional, KYC-gated, or fee-tier-restricted) deploys `SwapAllowlistExtension` to restrict who may trade. Any non-allowlisted user can bypass this control by calling `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on the pool. The user receives pool output tokens at oracle-anchored prices, draining liquidity that was reserved for allowlisted counterparties. This is a direct loss of curation policy and potentially of LP value if the pool was priced for a specific counterparty set.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who discovers the pool address can call it. No privileged access, no special setup, and no malicious initial configuration is required. The trigger is a single public transaction.

---

### Recommendation

The extension must gate the **economic actor** — the end user — not the intermediary. Two approaches:

1. **Pass the original user through the router**: The router could forward the original `msg.sender` as an additional field in `extensionData`, and the extension could decode and check it. This requires a convention between the router and the extension.

2. **Check `sender` against a router-aware allowlist**: The extension could maintain a separate mapping of trusted routers and, when `sender` is a trusted router, require the actual user identity to be supplied in `extensionData` and verified there.

The simplest correct fix is to ensure the extension always checks the address that is economically responsible for the swap — the address that ultimately pays the input tokens — rather than the address that called `pool.swap()`.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can use the standard router path.
3. Non-allowlisted attacker (address `eve`) calls:
     router.exactInputSingle({
       pool: pool,
       tokenIn: token0,
       recipient: eve,
       amountIn: X,
       ...
     })
4. Pool.swap() is called with msg.sender = router.
5. SwapAllowlistExtension.beforeSwap(sender=router, ...) checks
   allowedSwapper[pool][router] == true → passes.
6. Eve receives token1 output despite never being on the allowlist.
```

The allowlist is completely bypassed. The same path works for `exactInput`, `exactOutputSingle`, and `exactOutput`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
