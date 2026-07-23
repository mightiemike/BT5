### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. The extension therefore checks whether the router is allowlisted, not whether the actual user is allowlisted. A pool admin who allowlists the router to permit router-mediated swaps for approved users inadvertently opens the pool to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    zeroForOne,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router becomes the direct caller of `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The call chain is:

```
user → router.exactInputSingle()
     → pool.swap()          [msg.sender = router]
     → _beforeSwap(sender = router, ...)
     → extension.beforeSwap(sender = router)
     → allowedSwapper[pool][router]  ← checks router, not user
```

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that user is individually approved.

---

### Impact Explanation

Any user who is not on the swap allowlist can bypass the curation policy of a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The router is a public, permissionless contract. The bypass requires no special privileges, no malicious setup, and no non-standard tokens. The consequence is that the pool's intended access control is completely nullified for router-mediated swaps, allowing unauthorized users to trade against LP funds on a pool that was designed to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery path for end-user swaps. Any pool that deploys `SwapAllowlistExtension` and also wants its allowlisted users to be able to use the router must add the router to the allowlist. This is the natural, expected configuration. The bypass is therefore reachable on every such pool without any special precondition beyond the attacker knowing the router address.

---

### Recommendation

The extension must resolve the originating user rather than the immediate pool caller. Two approaches:

1. **Pass the originating user explicitly**: Require callers to supply the real swapper address in `extensionData` and verify it against a signature or a trusted forwarder registry. The extension decodes and checks that address instead of `sender`.

2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData` and check that address instead.

The simplest safe fix is to remove router support from the allowlist model entirely and require users to call `pool.swap()` directly when the pool is allowlisted, documenting that router-mediated swaps are incompatible with `SwapAllowlistExtension`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; alice is allowlisted, bob is not.
// Pool admin allowlists the router so alice can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), alice, true);
// bob is NOT allowlisted

// Bob bypasses the allowlist via the router:
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token1),
        recipient: bob,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Succeeds: extension sees sender=router, router is allowlisted → bob trades freely.
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which passes `allowedSwapper[pool][router]`, and the swap executes for `bob` despite him not being on the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```
