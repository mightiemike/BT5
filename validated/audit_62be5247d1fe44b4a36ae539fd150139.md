Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` as seen by the pool — the router contract when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (the only way to let legitimate users reach the pool via the canonical periphery), every unpermissioned user can bypass the allowlist by routing through the same contract. There is no configuration that simultaneously permits intended users to use the router and blocks unintended users.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received — the router address when routing through `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original user's identity — `msg.sender` to the pool is the router: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist correctly checks `owner` (the position owner, the economically relevant party), not `sender` (the caller): [5](#0-4) 

The swap allowlist should analogously check the actual swapper (e.g., `recipient`, who receives output tokens and is set by the user even when routing through the router), but instead checks the intermediary contract.

**The dilemma the pool admin faces:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user, allowlisted or not, bypasses the gate via the router |

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` for regulatory compliance, KYC gating, or market-maker restriction, and allowlists the router to enable normal periphery usage, inadvertently opens the gate to every user. Any non-allowlisted address can execute swaps against the pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. This is a direct admin-boundary break: an admin-configured access control is bypassed by an unprivileged path through a supported periphery contract. The allowlist provides zero protection on the router path.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool admin who wants allowlisted users to use the standard router must allowlist the router address — this is the natural, expected configuration. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges or preconditions. No exploit setup beyond calling the public router function is required.

## Recommendation
The extension must verify the actual end-user identity, not the intermediary. Two viable approaches:

1. **Align with the deposit allowlist pattern:** Gate by `recipient` (the address receiving output tokens) rather than `sender`, since `recipient` is the economically relevant party for a swap and is set by the user even when routing through the router. This requires no changes to the router or core.

2. **Pass the original user through the router:** Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it, with a trust assumption that the extension only accepts this encoding from a known, allowlisted router address.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - Router calls pool.swap(bob, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite not being on the allowlist
```

`bob` receives pool output tokens. The allowlist is completely ineffective for any user routing through the supported periphery.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
