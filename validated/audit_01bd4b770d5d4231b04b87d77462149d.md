### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the end user. A pool admin who allowlists the router to support router-mediated swaps simultaneously opens the allowlist to every user on the network.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the value the pool forwards from its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-L240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with `msg.sender = router`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-L80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all; they must call `pool.swap()` directly.
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through the router.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

Any non-allowlisted user can execute swaps on a curated pool that is supposed to restrict trading to a specific set of addresses (e.g., KYC'd wallets, protocol-internal actors). The allowlist protection silently fails open for all router-mediated swaps. This is a direct policy bypass that breaks the core invariant of the `SwapAllowlistExtension`: "only allowlisted addresses may swap on this pool."

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery swap entry point.
- Any user can call it permissionlessly.
- A pool admin who deploys a curated pool and wants to support router-mediated swaps for allowlisted users has no choice but to allowlist the router, which opens the gate to everyone.
- No privileged access, no special setup, and no non-standard tokens are required.

### Recommendation

Pass the **original end user** through the extension chain rather than the immediate `msg.sender` of `pool.swap()`. One approach: the router stores the originating user in transient storage (it already uses transient storage for callback context) and the pool reads it as an additional `originator` field forwarded to extensions. Alternatively, the extension can be redesigned to check `sender` only when `sender` is not a known router, and check a router-forwarded user field otherwise. At minimum, document that allowlisting the router defeats the allowlist and provide a router variant that forwards the originating user.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack (Bob, not allowlisted):
  1. Bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  → passes
  5. Bob's swap executes on the curated pool despite Bob never being allowlisted

Result:
  - allowedSwapper[pool][bob] == false, yet Bob swapped successfully
  - The allowlist invariant is broken for all router-mediated swaps
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-241)
```text
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
