### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` seen by the pool is the router contract, not the end user. If the pool admin allowlists the router (a necessary step for any allowlisted pool to support router-mediated swaps), every unpermissioned address can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is whatever the pool received as `msg.sender` of its own `swap()` call. The pool's `swap` interface takes no explicit `sender` parameter:

```solidity
function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
) external returns (int128 amount0Delta, int128 amount1Delta);
``` [2](#0-1) 

The pool therefore uses `msg.sender` as the `sender` it forwards to `_beforeSwap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the actual end-user address is stored only in transient storage for the payment callback and is never forwarded to the pool as `sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

Consequently, the extension's allowlist lookup becomes `allowedSwapper[pool][router]`. The admin has two losing options:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (multi-hop, exact-output, etc. are broken) |
| **Allowlist the router** | Every address on the network can bypass the allowlist by calling the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks unauthorized users.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is typically deployed to restrict swaps to a curated set of counterparties (e.g., to prevent uninvited arbitrage, enforce KYC, or limit LP exposure). Once the admin allowlists the router — a necessary operational step — any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against the pool. Unauthorized arbitrageurs can drain LP value at oracle-fair prices, and the allowlist invariant is silently violated with no on-chain signal. The `CallExtension` library does verify the `bytes4` selector return value, so the extension itself is called and returns normally; the bypass is structural, not a missing revert check. [6](#0-5) 

---

### Likelihood Explanation

The router is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to perform multi-hop or exact-output swaps must allowlist the router. The bypass is then reachable by any address with no special privilege, no flash loan, and no unusual token behavior. The trigger is a standard `exactInputSingle` call.

---

### Recommendation

The pool should forward the original caller's identity to extensions as a distinct field, separate from the direct `msg.sender`. One concrete approach: add an explicit `originator` parameter to the `beforeSwap` hook signature and have the router encode `msg.sender` into `extensionData` so the extension can decode and check it. Alternatively, the `SwapAllowlistExtension` should document clearly that it gates the **direct pool caller** (not the end user) and that allowlisting the router opens the gate to all users, so admins can make an informed decision.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only intended swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Pool calls extension.beforeSwap(sender=router, ...)
6. Extension checks allowedSwapper[pool][router] == true  → passes
7. Bob's swap executes; allowlist is bypassed.
8. Bob can repeat arbitrarily, extracting LP value.
```

The `SwapAllowlistExtension.beforeSwap` check at line 37 passes because `sender` is the router address, which is allowlisted, regardless of who the actual end-user is. [7](#0-6)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
