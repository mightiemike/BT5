### Title
`SwapAllowlistExtension` Checks Router Identity Instead of Original Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` is the router address, not the original user. A pool admin who allowlists the router to support router-mediated swaps for their allowlisted users inadvertently opens the allowlist to every user, because the extension sees the router's address (allowlisted) rather than the actual swapper's address (not allowlisted).

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool populates `sender` with `msg.sender` of the `swap` call inside `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol:88-98
function _beforeAddLiquidity(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_ADD_LIQUIDITY_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`) calls `pool.swap(...)`, the pool's `msg.sender` is `address(router)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:71-80
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

The extension therefore evaluates `allowedSwapper[pool][address(router)]`, not `allowedSwapper[pool][original_user]`.

**The bypass path**: A pool admin who wants to restrict swaps to specific users (e.g., KYC-verified addresses) AND support router-mediated swaps for those users will naturally allowlist the router address via `setAllowedToSwap(pool, address(router), true)`. Once the router is allowlisted, every user — including non-allowlisted ones — can call `router.exactInputSingle(...)` and pass the allowlist check, because the extension sees the router's allowlisted address as `sender`.

The `extensionData` field is forwarded from the router to the pool to the extension unchanged, but `SwapAllowlistExtension` ignores it entirely, so there is no mechanism for the router to convey the original user's identity to the guard. [4](#0-3) 

---

### Impact Explanation

Any unprivileged user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter` when the router is allowlisted. Consequences include:

- **Adverse-selection LP loss**: If the pool is restricted to trusted market makers to prevent toxic flow, unauthorized arbitrageurs can drain LP value through the router.
- **Broken core guard**: The swap allowlist — the primary access-control mechanism for restricted pools — silently fails open for all router-mediated swaps.
- **Compliance violation**: Pools restricted for regulatory reasons (e.g., only KYC'd counterparties) are exposed to unrestricted public access.

This matches the allowed impact gate: "Broken core pool functionality causing loss of funds" and "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to allowlist the router, which is the natural and expected configuration step for any pool that wants to support router-mediated swaps for its allowlisted users. The system provides no warning that allowlisting the router opens the gate to all users. A pool admin following the obvious operational pattern (allowlist users + allowlist the router) will unknowingly enable the bypass.

---

### Recommendation

1. **Router-forwarded identity**: The router should encode the original `msg.sender` into `extensionData` (e.g., as the first 20 bytes), and `SwapAllowlistExtension` should decode and check that identity when `sender` is a known router.
2. **Extension redesign**: Add a separate `allowedRouter` mapping; when `sender` is an allowlisted router, decode the original user from `extensionData` and check that address against `allowedSwapper`.
3. **Documentation warning**: At minimum, document that allowlisting the router address opens the allowlist to all users, so pool admins can make an informed choice.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls:
       setAllowedToSwap(pool, alice, true)          // Alice is the intended user
       setAllowedToSwap(pool, address(router), true) // to let Alice use the router
3. Charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: charlie, ...})
4. Router calls pool.swap(charlie, ...) with msg.sender = address(router).
5. Pool calls extension.beforeSwap(address(router), charlie, ...).
6. Extension checks allowedSwapper[pool][address(router)] == true → passes.
7. Charlie's swap executes against the restricted pool.
   Expected: revert NotAllowedToSwap.
   Actual:   swap succeeds.
``` [5](#0-4) [6](#0-5) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-42)
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
