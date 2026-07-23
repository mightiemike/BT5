### Title
SwapAllowlistExtension Gates Direct Pool Caller Instead of Ultimate User, Enabling Router-Mediated Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the direct caller of `pool.swap()` — against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, the router becomes the direct caller, so the extension checks the router address rather than the actual user. If the router is allowlisted (required for any router-mediated swaps to work for legitimate users), any user can bypass the allowlist by routing through the router.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (the pool calls the extension). `sender` is the first argument, which flows from `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

And in `MetricOmmPool.swap()`, `sender` is bound to `msg.sender` of the pool call:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, amountSpecified, priceLimitX64, ...);
``` [3](#0-2) 

So `sender` in the extension = `msg.sender` in `pool.swap()` = the **direct caller of `pool.swap()`**, not the ultimate user.

When a user calls `MetricOmmSimpleRouter.exactInput(...)`, the router calls `pool.swap()`, making the router the direct caller. The extension then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for pool admins:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — forced to call `pool.swap()` directly |
| Router **allowlisted** | **Any** user can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users. [4](#0-3) 

---

### Impact Explanation

Any user can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`. The allowlist is the sole access-control mechanism for curated pools. If it is bypassed:

- Non-allowlisted users can trade in a pool designed for specific, trusted counterparties (e.g., a private market-making pool or a regulatory-compliant pool).
- LP funds are exposed to unauthorized counterparties whose trading behavior the pool was not designed to accommodate.
- The pool admin's curation intent is entirely defeated — the allowlist provides no protection once the router is allowlisted.

This is a direct loss-of-policy-control with fund-impacting consequences: LP principal is exposed to unauthorized swap flow.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no elevated role, and no unusual token behavior. [5](#0-4) 

---

### Recommendation

The extension must check the **ultimate user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the originating user address in `extensionData`. The extension decodes and checks that address. The pool admin allowlists users, not the router.
2. **Sender-only enforcement**: Document explicitly that the allowlist only works for direct `pool.swap()` calls and that the router must never be allowlisted. Add a comment or revert path that detects known router addresses.

The `DepositAllowlistExtension` has a related but distinct issue: it checks `owner` (a caller-supplied parameter), meaning any address can call `pool.addLiquidity(allowedUser, ...)` and pass the allowlist check while paying tokens on behalf of the allowlisted owner. This is a griefing vector rather than a profitable bypass, but it similarly misidentifies the economically relevant actor. [6](#0-5) 

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the standard UX path.
4. Bob (non-allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. Router calls `pool.swap(recipient=bob, ...)` — router is `msg.sender`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully in the curated pool, bypassing the allowlist entirely. [7](#0-6) [1](#0-0)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
