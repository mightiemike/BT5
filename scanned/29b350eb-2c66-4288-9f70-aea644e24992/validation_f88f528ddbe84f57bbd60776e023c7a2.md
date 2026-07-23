### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the router's address rather than the end user's address. If the router is allowlisted (which is required for any legitimate user to use it), every unpermissioned user can bypass the curated pool's swap restriction by routing through the router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its check as:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument the pool passes, which is always `msg.sender` of the `pool.swap()` call:

```solidity
// MetricOmmPool.sol (simulateSwapAndRevert shown; swap is identical)
_beforeSwap(
    msg.sender,   // ← always the direct caller of the pool
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exact*()`, the router calls `pool.swap(...)` with itself as `msg.sender`. The pool therefore passes the **router address** as `sender` to the extension. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This creates an irreconcilable dilemma for the pool admin:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Legitimate allowlisted users cannot use the router at all |
| Router **allowlisted** | Every user, including non-allowlisted ones, can bypass the allowlist through the router |

Because `MetricOmmSimpleRouter` is the canonical supported periphery swap path, the pool admin must allowlist it to give legitimate users a usable interface. Doing so opens the allowlist to every caller.

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified wallets, protocol-controlled addresses). Any non-allowlisted user can bypass this restriction by calling `MetricOmmSimpleRouter` instead of the pool directly. The router is allowlisted as a single address, so the allowlist provides zero protection against router-mediated swaps. Unauthorized users can drain arbitrage value, execute trades on restricted pools, and undermine any compliance or access-control guarantee the pool admin intended to enforce.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and supported by the protocol. Any user aware of the router can trivially exploit this by calling `exactInput` or `exactOutput` instead of `pool.swap` directly. No special privileges, flash loans, or multi-step setup are required. The trigger is a single public transaction.

### Recommendation

The pool must pass the **original end user** as `sender` to extensions, not the intermediate router. Two approaches:

1. **Router forwards the originating user**: `MetricOmmSimpleRouter` passes `msg.sender` (the end user) as a `sender` override parameter to `pool.swap`, and the pool accepts an explicit `sender` argument that extensions receive. This mirrors how Uniswap v4 handles hook `msgSender`.

2. **Extension reads `tx.origin` or a trusted forwarder context**: Less preferred due to `tx.origin` risks, but the extension could accept a signed proof of the real sender from the router.

The simplest safe fix is option 1: add a `sender` parameter to `pool.swap` that the pool passes through to extensions, defaulting to `msg.sender` for direct calls, and having the router supply `msg.sender` (the end user) explicitly.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the legitimate user)
  - allowedSwapper[pool][router] = true  (required so alice can use the router)

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(pool, ...)
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - Bob bypasses the curated pool's swap allowlist
  - Any user can do the same; the allowlist is entirely ineffective for router-mediated swaps
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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
