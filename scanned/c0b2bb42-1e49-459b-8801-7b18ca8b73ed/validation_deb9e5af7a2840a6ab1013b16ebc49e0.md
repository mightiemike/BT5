### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Any User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the router's address, not the original user's address. If the pool admin allowlists the router (which is required for any router-based swap to succeed on a curated pool), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput(...)`, the router calls `pool.swap(...)` directly. From the pool's perspective `msg.sender` is the router, so the extension receives `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

For router-based swaps to work at all on a pool that has `SwapAllowlistExtension` configured, the pool admin must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every caller regardless of whether the original user is on the allowlist.

The `SwapAllowlistExtension` is designed to gate the exact actor the pool intends to restrict: [4](#0-3) 

But the actor actually checked is the intermediate router, not the economic actor executing the trade.

---

### Impact Explanation

Any user who is not on the swap allowlist of a curated pool can bypass the restriction entirely by routing through `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may swap on this pool" — is completely broken for every pool that must also support router-based swaps. This is a direct policy bypass with fund-impacting consequences: disallowed users can execute swaps, drain liquidity at oracle prices, and interact with pools that were intended to be restricted to a known set of counterparties.

---

### Likelihood Explanation

The scenario is highly likely in practice:

1. A pool is deployed with `SwapAllowlistExtension` to restrict trading to approved counterparties.
2. The pool admin allowlists the router so that approved users can use the standard periphery path — a routine and expected configuration.
3. Any unpermissioned user observes the router is allowlisted (on-chain state is public) and calls `MetricOmmSimpleRouter` directly.
4. The extension sees `sender = router`, passes the check, and the swap executes.

No privileged access, no special setup, and no non-standard tokens are required. The attacker only needs to call the public router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the original user, not the intermediate router. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward the original `msg.sender` as an explicit `sender` argument to `pool.swap`, and the pool/extension interface should distinguish between the economic actor and the immediate caller.
2. **Extension-level forwarding**: Alternatively, encode the original user inside `extensionData` and have the extension decode and verify it, with the router signing or attesting to the original caller. This requires the extension to trust the router, which must itself be verified.

The simplest safe fix is to have the pool pass both `msg.sender` (the immediate caller) and an explicit `sender` parameter that the router populates with the original user, and have the allowlist check the explicit `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is approved
  allowedSwapper[pool][router] = true     // router allowlisted so alice can use it
  bob is NOT on the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle(pool, tokenIn, tokenOut, ...)
  2. Router calls pool.swap(recipient=bob, ...)
  3. pool._beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  5. Swap executes for bob despite bob not being allowlisted

Direct call check (for comparison):
  bob calls pool.swap(...) directly
  pool._beforeSwap(sender=bob, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → revert NotAllowedToSwap
```

The bypass is reachable on any production pool that has both `SwapAllowlistExtension` active and the router allowlisted — a configuration that is necessary for the pool to be usable through the standard periphery. [3](#0-2) [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-14)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

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
