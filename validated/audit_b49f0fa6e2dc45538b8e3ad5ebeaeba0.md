Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. A pool admin who allowlists the router (required for any legitimate user to use it) simultaneously opens the gate to every unprivileged address on-chain, fully defeating the allowlist invariant.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and passes it verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and forwards it to each configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool (`msg.sender` inside the extension is the pool): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call. The `extensionData` forwarded is the raw user-supplied `params.extensionData` — the router never encodes its own `msg.sender` (the real user) into it: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Because the extension sees `sender = router`, the allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice: if the router is not allowlisted, every allowlisted user is also blocked from using the canonical periphery path; if the router is allowlisted, every address on-chain can bypass the allowlist by routing through it. No configuration simultaneously permits legitimate router use and blocks non-allowlisted users. There is no existing guard that compensates — `CallExtension.callExtension` passes data as-is with no originator authentication: [6](#0-5) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) is fully bypassed. Any unprivileged address can execute swaps against the pool's liquidity at oracle-derived prices, draining LP value through adverse selection or extracting arbitrage that the allowlist was designed to prevent. This is a direct loss of LP principal and a broken core pool invariant — the `allowedSwapper` mapping no longer controls who can swap. [7](#0-6) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, documented swap entry point for end users. Any pool that deploys `SwapAllowlistExtension` and also needs to support router-based swaps (the normal operating mode) must allowlist the router, at which point the bypass is unconditional and requires no special setup. The trigger is a standard `exactInputSingle` call from any EOA. The precondition — router allowlisted — is the expected production configuration for any pool that intends to serve users through the periphery. [8](#0-7) 

## Recommendation
The extension must check the economically relevant actor — the address that initiated the transaction — not the immediate caller of the pool. The cleanest fix is for `MetricOmmSimpleRouter` to always encode `msg.sender` into `extensionData` (e.g., as the first 20 bytes), and for `SwapAllowlistExtension.beforeSwap` to decode and verify the real initiator from `extensionData` when `sender` is a known router address. Alternatively, redesign `beforeSwap` to accept an authenticated originator field, or require the router to forward the real user identity through a dedicated mechanism. Using `tx.origin` is a last resort with known limitations (breaks contract-to-contract flows). [3](#0-2) 

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed swapper
  allowedSwapper[pool][router] = true         // required so alice can use the router

Attack:
  eve (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      zeroForOne: true,
      amountIn: X,
      recipient: eve,
      extensionData: ""
    })

  router calls pool.swap(eve, true, X, ...)   // msg.sender = router
  pool calls _beforeSwap(router, eve, ...)
  extension checks allowedSwapper[pool][router] → true
  swap executes; eve receives output tokens

Result:
  eve swaps successfully against a pool that was supposed to block her.
  If router is NOT allowlisted, alice cannot use the router either —
  the supported periphery path is broken for the pool's own allowlisted users.
``` [1](#0-0) [9](#0-8) [10](#0-9)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-32)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
    if (result.length < 32) {
      revert InvalidExtensionResponse();
    }
    bytes4 returnedSelector;
    assembly ("memory-safe") {
      returnedSelector := mload(add(result, 32))
    }
    bytes4 expectedSelector;
    assembly ("memory-safe") {
      expectedSelector := mload(add(data, 32))
    }
    if (returnedSelector != expectedSelector) {
      revert InvalidExtensionResponse();
    }
  }
```
