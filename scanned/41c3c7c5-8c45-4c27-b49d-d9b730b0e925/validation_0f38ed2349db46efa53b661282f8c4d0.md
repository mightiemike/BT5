### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, so the extension checks whether the **router** is allowlisted rather than the actual user. Any non-allowlisted user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool and `sender` is the address the pool passes as the initiating swapper: [1](#0-0) 

The pool's `swap` function takes no explicit `sender` parameter — it uses `msg.sender` internally and forwards it as `sender` to `_beforeSwap`: [2](#0-1) 

The pool's `swap` interface confirms there is no caller-supplied `sender` field; the pool derives it from `msg.sender`: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The `_callExtensionsInOrder` dispatcher passes this router-derived `sender` unchanged to every configured extension: [4](#0-3) 

The `DepositAllowlistExtension` does **not** share this flaw because it gates on the explicit `owner` parameter (which the pool's `addLiquidity` accepts as a caller-supplied argument, separate from `msg.sender`): [5](#0-4) 

The swap path has no equivalent explicit-sender argument, making the router-mediated path structurally different from the deposit path.

---

### Impact Explanation

**High.** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant) targeting that pool. The router is a public, permissionless contract. The extension sees `sender = router` and checks `allowedSwapper[pool][router]`. If the router is allowlisted (to permit legitimate users to use it), the check passes for **all** callers — the allowlist is fully bypassed. If the router is not allowlisted, the check fails for **all** router-mediated swaps, breaking the supported periphery path for legitimate users. Either outcome violates the pool's intended access policy and constitutes a broken core pool functionality / admin-boundary break with direct fund-impact on curated pools.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the primary supported swap entrypoint documented in the protocol. Any user who discovers the allowlist on a curated pool can trivially route through the router. No privileged access, special tokens, or malicious setup is required — only a standard `exactInputSingle` call.

---

### Recommendation

The pool should forward the original initiating user as `sender` rather than `msg.sender`. Two approaches:

1. **Preferred:** Add an explicit `sender` parameter to `pool.swap`, validated via a trusted-caller pattern (e.g., only the factory-registered router may supply a different sender). The router passes `msg.sender` (the end user) as `sender`.

2. **Alternative:** The `SwapAllowlistExtension` should check `recipient` (the address that receives output tokens) instead of `sender` when the pool is known to be router-mediated, or the extension should be redesigned to accept an explicit user identity via `extensionData` that the router populates with `msg.sender`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow the router (necessary for any legitimate user to use the router).
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the curated pool.
4. The router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(router, recipient, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**.
6. Attacker's swap executes on a pool that was supposed to be restricted to specific users.

Alternatively, if the admin does **not** allowlist the router:

3. Allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Extension checks `allowedSwapper[pool][router]` → **reverts** with `NotAllowedToSwap`.
5. The supported periphery path is permanently broken for all legitimate users. [1](#0-0) [2](#0-1)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
