### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Allowlist Bypass Through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the address that called `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the actual user. This wrong-actor binding means the allowlist silently fails to gate the economically relevant actor: if the router is allowlisted (the only way to enable router-mediated swaps for legitimate users), every unprivileged address can bypass the allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is registered as a `beforeSwap` hook on curated pools. Its guard logic is:

```solidity
// SwapAllowlistExtension.sol L31-41
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
``` [1](#0-0) 

`msg.sender` here is the pool (correct), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(
    address sender,   // <-- this is msg.sender of pool.swap()
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

The pool passes `msg.sender` as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point) calls `pool.swap(...)`, `msg.sender` inside the pool is the **router**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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
``` [3](#0-2) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual user's address is available only as `params.recipient` (the output recipient), which the extension never inspects.

This creates two mutually exclusive failure modes:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot swap through the router — broken core swap flow |
| Router **allowlisted** (to fix the above) | Every unprivileged address can bypass the per-user allowlist by routing through the router |

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (the natural fix for "my allowlisted users can't use the router") inadvertently opens the pool to all users. Any address can call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool and the extension will pass because `allowedSwapper[pool][router] == true`. The allowlist — the sole access-control mechanism on the swap path — is silently nullified. Unauthorized users can trade on pools with restricted pricing or LP conditions, causing direct loss to LPs through adverse selection or unauthorized extraction of favorable quotes.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user with a token balance can call the router. The only precondition is that the pool admin has allowlisted the router, which is the expected operational step to make the router usable for their legitimate allowlisted users. The research target explicitly flags this exact scenario: "Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."

---

### Recommendation

The extension must check the identity of the actual end user, not the intermediary caller. Two options:

1. **Check `recipient` instead of `sender`**: The router passes the actual user as `recipient` in `pool.swap(recipient, ...)`. The extension should gate on `recipient` for swap allowlists, since that is the economically attributed actor.

2. **Require direct pool calls only**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this with an `onlyPool`-style check that also verifies `sender == tx.origin` or a similar direct-call guard (with appropriate caveats for contract wallets).

Option 1 is the cleaner fix:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` registered as `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Alice tries to swap through the router → reverts (`NotAllowedToSwap`) because `allowedSwapper[pool][router] == false`.
4. Pool admin calls `setAllowedToSwap(pool, router, true)` to fix Alice's router access.
5. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
6. Pool calls `_beforeSwap(sender=router, recipient=bob, ...)`.
7. Extension checks `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes on the restricted pool — allowlist fully bypassed. [1](#0-0) [4](#0-3) [2](#0-1)

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
