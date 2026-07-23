### Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Allowing Any User to Bypass the Per-Pool Allowlist Guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, the check passes for every user who routes through the router — completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks that `sender` (the pool's `msg.sender`) is allowlisted for the calling pool: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any multi-hop variant), the router becomes `msg.sender` inside `pool.swap()`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert for everyone, including legitimately allowlisted users |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the guard by routing through the public router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

The `ExtensionCalling._beforeSwap` dispatcher passes the pool's `msg.sender` without any mechanism to recover the original EOA: [3](#0-2) 

The router is a public, permissionless periphery contract callable by any address: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted strategies) provides no actual restriction once the router is allowlisted. Any unprivileged user can:

1. Call `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool.
2. The router calls `pool.swap()`; the extension sees `sender = router`, which is allowlisted.
3. The swap executes at the oracle-anchored price, draining LP assets to an unauthorized counterparty.

This breaks the core pool invariant that only allowlisted addresses may trade, constitutes a direct loss of LP principal to unauthorized swappers, and renders the allowlist guard entirely ineffective — matching the "admin-boundary break via unprivileged path" impact category.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is a public periphery contract; any user can call it.
- Pool admins who deploy `SwapAllowlistExtension` and want their allowlisted users to be able to use the router (the standard UX path) will naturally allowlist the router address.
- The bypass requires no special privileges, no flash loans, and no oracle manipulation — only a standard router call.
- The protocol documentation and extension interface give no indication that allowlisting the router opens the gate to all users.

---

### Recommendation

The `sender` forwarded to extensions must represent the economically relevant actor, not the intermediate contract. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should accept an explicit `swapper` parameter and pass it as `callbackData` or a dedicated field so the pool can forward the true originator to extensions.
2. **Extension-level**: `SwapAllowlistExtension` should accept an optional "true sender" encoded in `extensionData` and verify it when present, falling back to `sender` for direct pool calls.
3. **Guard-level**: Add a `require(sender != address(router), ...)` check or maintain a registry of known intermediaries whose `extensionData` must carry a verified originator.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, tokenIn: ..., tokenOut: ..., ...})
5. Router calls pool.swap(recipient=bob, ...)
6. Pool calls _beforeSwap(msg.sender=router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes; LP assets transferred to Bob.
```

Expected: revert `NotAllowedToSwap()`.
Actual: swap succeeds; Bob receives pool output tokens. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L14-22)
```text
/// @title MetricOmmPoolLiquidityAdder
/// @notice Routes `addLiquidity` for EOAs: the pool calls this contract in `metricOmmModifyLiquidityCallback`,
///         which pulls tokens from the user who must have approved this adder beforehand.
/// @dev Layout follows metric-core conventions:
///      constants/state, constructor, external mutators, then internal helpers.
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
contract MetricOmmPoolLiquidityAdder is IMetricOmmPoolLiquidityAdder, PeripheryPayments {
```
