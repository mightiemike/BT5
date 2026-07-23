### Title
SwapAllowlistExtension Allowlist Bypassed via Router: Any User Can Swap on Restricted Pools When Router Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end user. If the pool admin allowlists the router (a natural operational step to enable UI/router access for legitimate users), every non-allowlisted user can bypass the gate by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes `msg.sender` as `sender` into `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

So `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

The pool admin faces an impossible choice:
- **Do not allowlist the router**: legitimate allowlisted users cannot use the router at all (their swaps revert because `sender = router` is not allowlisted).
- **Allowlist the router**: every non-allowlisted user can bypass the gate by routing through the router.

There is no configuration that simultaneously allows specific users to swap via the router while blocking others. The guard is structurally broken for router-mediated paths.

### Impact Explanation

Any non-allowlisted user can execute swaps on a pool that the admin intended to restrict. Restricted pools are typically deployed for specific counterparties (e.g., institutional LPs, whitelisted market makers). Unauthorized swaps allow:
- Extraction of LP value at oracle-anchored prices by parties the pool was designed to exclude.
- Disruption of pool composition and bin state by unauthorized actors.
- Effective nullification of the `SwapAllowlistExtension` guard whenever the router is in use.

This is a direct loss of LP principal through unauthorized trading on a pool whose access control is silently bypassed.

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who deploys a restricted pool and also wants their allowlisted users to use the standard router UI will naturally allowlist the router address. This is the expected operational pattern. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges.

### Recommendation

The extension must check the **end user identity**, not the intermediary. Two approaches:

1. **Pass end-user identity through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to not forge the identity.

2. **Check `sender` and fall back to `extensionData` for router-mediated calls**: If `sender` is a known router, decode the real swapper from `extensionData` and check that address instead.

3. **Gate on `recipient` instead of `sender`** if the pool's intent is to restrict who receives output tokens (though this changes semantics).

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` as part of `extensionData`, and the extension verifies both that `sender` is the trusted router and that the decoded end user is allowlisted.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // admin allowlists router so alice can use the UI
4. bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           tokenIn: token0,
           zeroForOne: true,
           amountIn: 1000,
           amountOutMinimum: 0,
           ...
       })
5. pool.swap() is called with msg.sender = router
6. _beforeSwap passes sender = router to SwapAllowlistExtension
7. allowedSwapper[pool][router] == true  →  check passes
8. bob's swap executes successfully despite not being allowlisted
9. bob extracts token1 from the pool at oracle price, bypassing the intended access control
``` [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
