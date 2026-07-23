### Title
`SwapAllowlistExtension` gates on the direct pool caller (`sender`) rather than the end user, allowing any user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension::beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool::swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the router is allowlisted for a pool (which is required for any router-mediated swap to succeed), every user — including those not individually allowlisted — can bypass the swap allowlist by calling the router instead of the pool directly.

### Finding Description

`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling::_beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, ...)   // sender = msg.sender of pool.swap()
    )
);
```

`SwapAllowlistExtension::beforeSwap` then checks whether that `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol line 71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The result is:
- **Direct call** (`user → pool.swap()`): extension sees `sender = user` → checks `allowedSwapper[pool][user]` ✓
- **Router call** (`user → router.exactInputSingle() → pool.swap()`): extension sees `sender = router` → checks `allowedSwapper[pool][router]`

The pool admin faces an impossible choice:
1. **Allowlist the router** → every user (including non-allowlisted ones) can bypass the guard by routing through the router.
2. **Do not allowlist the router** → individually allowlisted users cannot use the router at all.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified users, institutional counterparties) is fully bypassed by any user who calls `MetricOmmSimpleRouter::exactInputSingle` or `exactInput`. The router is a public, permissionless contract with no access control. Once the router is allowlisted (which is necessary for any router-mediated swap to work), the allowlist provides no protection against unauthorized swappers. Unauthorized users can execute swaps at oracle-anchored prices, draining LP value from a pool that was intended to be curated.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool operator who deploys a `SwapAllowlistExtension` and also wants users to be able to use the router must allowlist the router, at which point the bypass is trivially reachable by any address. No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices.

### Recommendation

The `SwapAllowlistExtension` should receive the end user's identity rather than the direct pool caller. One approach is to have the router forward the original `msg.sender` through `extensionData`, and have the extension decode and check that value. Alternatively, the pool could pass both `sender` (direct caller) and an optional `originator` field through the hook interface so extensions can gate on the economically relevant actor. At minimum, the NatSpec for `SwapAllowlistExtension` must warn that allowlisting the router grants unrestricted access to all users.

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that legitimate users can swap via router.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// She calls the router directly — the extension sees sender = router, which IS allowlisted.
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: alice,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Alice's swap succeeds despite not being on the allowlist.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
