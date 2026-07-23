All four cited files are confirmed in the repository. The code matches the claim exactly:

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()` — confirmed at line 37.
- `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at lines 230–231.
- `ExtensionCalling._beforeSwap` forwards `sender` unchanged — confirmed at lines 160–176.
- `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` — confirmed at lines 72–80.

The exploit path is real, preconditions are minimal (pool admin allowlists the router, which is the natural configuration), and no privileged attacker capability is required.

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router so that approved users can use it inadvertently grants every user on the network unrestricted swap access to the pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder` (lines 160–176). `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes the direct caller of `pool.swap()` (lines 72–80 of `MetricOmmSimpleRouter.sol`), so `sender = address(router)`. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. For any pool that allowlists the router (required for approved users to use it), the check passes for every caller regardless of individual approval status. No existing guard resolves the originating user from `extensionData` or any other source.

## Impact Explanation
The `SwapAllowlistExtension`'s access control is completely nullified for router-mediated swaps. Any non-allowlisted user can trade against LP funds on a pool that was designed to be restricted, bypassing the pool admin's curation policy. This constitutes broken core pool functionality (access control bypass allowing unauthorized swap execution) and an admin-boundary break where an unprivileged path circumvents the pool admin's intended restriction.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery path for end-user swaps. Any pool deploying `SwapAllowlistExtension` that also wants approved users to use the router must add the router to the allowlist — this is the natural, expected configuration. Once the router is allowlisted, the bypass is reachable by any user who knows the router address, with no special privileges, no malicious setup, and no non-standard token behavior required. The condition is met on every such pool.

## Recommendation
The extension must resolve the originating user rather than the immediate pool caller. Options:
1. **Trusted router registry**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData` and check that address instead.
2. **Explicit originator in extensionData**: Require callers to supply the real swapper address in `extensionData` with a verifiable signature; the extension decodes and checks that address.
3. **Prohibit router use with allowlist**: Document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps; require allowlisted users to call `pool.swap()` directly.

## Proof of Concept
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
