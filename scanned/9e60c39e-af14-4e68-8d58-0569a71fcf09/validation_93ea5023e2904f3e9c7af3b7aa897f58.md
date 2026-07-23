### Title
`SwapAllowlistExtension` checks the router's address instead of the originating EOA, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating EOA. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, forwarding its own `msg.sender` as the `sender` argument to every configured extension. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The allowlist therefore checks `allowedSwapper[pool][router]`, not the originating EOA. A pool admin who wants to support router-based swaps for permitted users must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller regardless of who they are, because the router is a public, permissionless contract that any EOA can invoke.

The same identity collapse occurs in the multi-hop `exactInput` path for intermediate hops, where `sender` becomes `address(this)` (the router itself): [5](#0-4) 

There is no mechanism in `SwapAllowlistExtension` to recover the originating EOA; the extension has no access to `tx.origin` and the extension data forwarded by the router carries no identity proof. [6](#0-5) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting which counterparties can trade against a pool's LP positions. Pools that deploy it are typically designed to limit adverse selection — e.g., institutional or curated pools that only accept known, non-toxic flow. When the guard is bypassed, every unpermitted EOA gains full swap access. LPs in such pools suffer the adverse selection losses the allowlist was designed to prevent: informed traders and MEV bots can extract value from the pool at oracle-quoted prices, directly reducing LP principal. This is a direct loss of LP assets above Sherlock thresholds in any pool where the allowlist is the primary protection against toxic flow.

---

### Likelihood Explanation

The bypass is reachable by any unpermitted user with no special privileges. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool that intends to support router-based swaps for its permitted users. The admin has no way to allowlist specific users *for router-mediated swaps* without also opening the gate to everyone, so the misconfiguration is structurally forced. The `MetricOmmSimpleRouter` is a public, deployed periphery contract with no access control of its own.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor — the originating EOA — not the immediate caller. Two options:

1. **Extension-data identity proof**: Require the router to encode the originating `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a trusted-router registry to prevent spoofing).
2. **Separate router allowlist**: Introduce a second mapping `allowedRouter` and, when `sender` is a known router, require the router to attest the originating user via a signed or encoded payload that the extension validates.

The simplest safe default is to treat any unrecognized `sender` (including the router) as unpermitted unless the pool has explicitly set `allowAllSwappers = true`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is a permitted user
  admin calls setAllowedToSwap(pool, router, true)      // router allowlisted to support alice's router swaps

Attack:
  bob (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
        msg.sender of pool.swap() = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result:
  bob bypasses the allowlist entirely.
  Every unpermitted EOA can do the same.
  LPs are exposed to unrestricted toxic flow.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-42)
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
