### Title
`SwapAllowlistExtension::beforeSwap` checks the router's address instead of the actual swapper, allowing any non-allowlisted user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` parameter passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap` receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router (required for allowlisted users to use the router at all) inadvertently opens the gate to every user on the internet.

---

### Finding Description

`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension::beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter::exactInputSingle`, the router calls `pool.swap(recipient, ...)` directly:

```solidity
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

The pool sees `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's identity is never visible to the extension.

A pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. The moment they do, every address on the internet can call `router.exactInputSingle(pool, ...)` and the extension passes them through, because `sender = router` is allowlisted.

There is no mechanism in the router or the extension interface to thread the original `msg.sender` through to the hook. The `extensionData` bytes are user-controlled and the extension ignores them entirely.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to enforce KYC, regulatory, or institutional-only access is fully bypassed for any user who routes through the public `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, extract tokens from the pool at oracle prices, and drain LP value. This is a direct loss of LP principal and a broken core pool invariant (access-controlled swap policy is not enforced).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want their own allowlisted users to be able to use the router. The bypass therefore activates as soon as the pool is configured for normal router-mediated use.

---

### Recommendation

The extension must check the economically relevant actor, not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it. This requires a coordinated change to the router and the extension.

2. **Check `sender` in the pool, not the extension**: The pool could expose a "real sender" concept (e.g., via transient storage set by the router before calling `swap`), and the extension reads that instead of the forwarded `sender` parameter.

The simplest safe rule: if `SwapAllowlistExtension` is configured on a pool, the router must never be allowlisted as a blanket entry. Instead, the extension must be redesigned so that router-mediated swaps carry the originating user's identity to the hook.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// allowlist alice (KYC'd user)
ext.setAllowedToSwap(pool, alice, true);
// allowlist the router so alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// bob (NOT allowlisted) bypasses the guard via the router
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
// extension sees sender = router (allowlisted), not bob — swap succeeds
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            pool,
        tokenIn:         token0,
        recipient:       bob,
        zeroForOne:      true,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// bob received token1 from a pool he is not allowed to trade on
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
