Audit Report

## Title
SwapAllowlistExtension Per-User Allowlist Bypassed via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and passes that as `sender` to the extension. If the pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the public router contract, because the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

The pool always passes its own `msg.sender` as that first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the originating EOA
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the originating user's address:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The originating user's address is stored only in transient storage for the payment callback; it is never surfaced to the pool or the extension. The same structural gap exists in `exactInput` (multi-hop) and `exactOutput` paths. [4](#0-3) 

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only specific trusted addresses.
2. To also permit those users to swap via the router (a natural UX requirement), the admin calls `setAllowedToSwap(pool, router, true)`.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The pool calls `_beforeSwap(router, ...)`, the extension evaluates `allowedSwapper[pool][router] == true`, and the swap proceeds — bypassing the per-user allowlist entirely.

The extension provides no mechanism to distinguish which EOA initiated the router call, making it structurally impossible to enforce per-user allowlisting for router-mediated swaps. [5](#0-4) 

## Impact Explanation
The allowlist extension's core invariant — that only explicitly approved addresses may swap in a gated pool — is broken for any pool that allowlists the router. Unauthorized users gain unrestricted swap access to pools intended to be restricted (e.g., institutional-only or KYC-gated pools). This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path bypasses the access control the pool admin configured. Severity: **Medium** (access control bypass enabling unauthorized swaps; direct fund loss depends on pool configuration but the control failure itself meets Sherlock thresholds for broken invariants).

## Likelihood Explanation
The precondition — admin allowlisting the router — is a natural and expected operational step for any pool that wants to support router-mediated swaps while still restricting direct swappers. Any unprivileged user can then exploit this by simply calling the public router. No special privileges, flash loans, or complex setup are required. The attack is repeatable and permissionless.

## Recommendation
The extension must verify the originating EOA, not the intermediate contract. Options:
1. **Pass originator through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. This requires the extension to trust that the router correctly reports the originator.
2. **Originator forwarding in pool interface**: Add an explicit `originator` field to the swap call that the pool passes to the extension alongside `sender`, allowing the extension to check the true initiating address.
3. **Router-aware allowlist**: Add a separate `allowedRouter` mapping and, when `sender` is a known router, require the extension to also verify the originator via a trusted callback or transient storage slot written by the router before the swap.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists router: extension.setAllowedToSwap(pool, address(router), true)
// 3. Attacker (not individually allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: allowlistedPool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// 4. Pool calls _beforeSwap(router, ...) → extension checks allowedSwapper[pool][router] == true → swap succeeds
// 5. Assert: attacker received token1 output despite not being individually allowlisted
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
