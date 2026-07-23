Audit Report

## Title
SwapAllowlistExtension Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is the direct caller, the extension evaluates the router's allowlist entry, not the end user's. Any pool admin who allowlists the router (required for allowlisted users to reach the pool via the router) simultaneously grants every non-allowlisted user unrestricted swap access through that same router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (enforced by the `onlyPool` modifier in `BaseMetricExtension`) and `sender` is the first argument forwarded by the pool. [2](#0-1) 

`MetricOmmPool.swap` sets that `sender` argument to its own `msg.sender` — the direct caller of `pool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
    recipient, ...
);
``` [3](#0-2) 

`ExtensionCalling._beforeSwap` passes this `sender` value unchanged into the extension call: [4](#0-3) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the original `msg.sender` into `extensionData`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [5](#0-4) 

The router stores the original `msg.sender` only in transient storage for the payment callback (`_setNextCallbackContext`), not in `extensionData`. The extension never sees the end user's address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the router is allowlisted, every caller of the router passes the check regardless of their own allowlist status.

## Impact Explanation
LPs in curated pools (KYC-gated, institutional, or counterparty-restricted) suffer unauthorized swaps against their positions by non-allowlisted users. Because the pool is oracle-driven and the attacker controls timing and direction, this constitutes unauthorized execution against LP positions — a direct loss of LP principal through adverse selection. This satisfies the "broken core pool functionality causing loss of funds" and "admin-boundary break: allowlist policy bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The bypass requires only that the router be allowlisted for the target pool — the exact configuration any pool admin must apply to let their allowlisted users access the router. No special privilege is needed: any EOA can call `MetricOmmSimpleRouter.exactInputSingle()`. The router is a public, factory-validated contract. The condition is self-fulfilling: the bypass is enabled by the same admin action that enables legitimate router use.

## Recommendation
The extension must check the ultimate end user, not the intermediate router. Two approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it when the direct `sender` is a known router. This requires a coordinated change to the router and extension.
2. **Structured `extensionData` payload**: The extension accepts a structured payload carrying the original user address when the router is the direct caller, and falls back to `sender` for direct pool calls. The router must be updated to always populate this field.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so Alice can use it
4. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender in pool = router
6. Pool calls _beforeSwap(sender=router, ...)
7. ExtensionCalling passes sender=router to extension.beforeSwap(router, ...)
8. Extension checks allowedSwapper[pool][router] == true → passes
9. Charlie's swap executes against LP positions.
```

Charlie never appears in the allowlist. The check at `SwapAllowlistExtension.sol` L37 evaluates the router's address, which is allowlisted, so the guard silently passes for any caller who routes through the router. [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
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
