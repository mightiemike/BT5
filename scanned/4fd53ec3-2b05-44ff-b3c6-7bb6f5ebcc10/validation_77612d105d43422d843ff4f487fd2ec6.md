### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End-User Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes its own `msg.sender` as `sender` to the hook. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore gates the router's address, not the actual trader. Any non-allowlisted user can bypass a curated pool's swap gate simply by calling the public router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the only caller permitted by `onlyPool`), and `sender` is the value the pool forwarded. The pool's `swap` function passes its own `msg.sender` as the `sender` argument to `_beforeSwap` (and then on to every configured extension). When a user calls `MetricOmmSimpleRouter.exactInput` / `exactOutput`, the router is the pool's `msg.sender`. The hook therefore evaluates:

```
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][end_user]
```

Two exploitable scenarios follow:

1. **Allowlist bypass (primary impact):** The pool admin allowlists the router address (a natural operational choice so that normal users can trade). Every non-allowlisted address can now swap on the curated pool by routing through the public router, because the hook sees `router` — which is allowlisted — rather than the actual trader.

2. **Allowlisted users locked out:** If the admin does *not* allowlist the router, allowlisted users who try to swap through the router are rejected because the hook sees the non-allowlisted router address.

The `DepositAllowlistExtension` does not share this flaw: it checks `owner` (the position owner supplied by the caller), which is the economically relevant identity for deposits regardless of who pays.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). The bypass lets any arbitrary address trade on such a pool by routing through `MetricOmmSimpleRouter`. This directly violates the pool's curation invariant and can result in:

- Unauthorized parties draining liquidity from a restricted pool.
- Protocol or admin policy (e.g., regulatory compliance, partner-only pools) being silently circumvented.
- LP funds being exposed to traders the pool was explicitly designed to exclude.

This is a **High** severity direct-loss / broken-core-functionality finding: the allowlist guard fails open for every router-mediated swap.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public-facing swap entrypoint documented and deployed for end users. Any non-allowlisted user who discovers the bypass (or simply uses the standard router) can exploit it immediately without any privileged access, special tokens, or setup. The router is a fixed, known address, making the bypass trivially reproducible.

---

### Recommendation

The `beforeSwap` hook must check the **end-user identity**, not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`:** The router encodes the true `msg.sender` (the end user) into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to populate the field honestly — acceptable since the router is a protocol-controlled contract.

2. **Check `recipient` instead of `sender` when the sender is a known router:** The pool already passes `recipient` as a separate argument. If the pool's design intent is that `recipient` is always the economic beneficiary of the swap, the extension can gate on `recipient`. However, `recipient` can also be set to an arbitrary address by the caller, so this requires careful analysis of the pool's swap semantics.

3. **Require direct pool calls for allowlisted pools:** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; users must call the pool directly. This is operationally fragile but avoids code changes.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (or setAllowAllSwappers(pool, false) and allowlists only specific users).
  - Alice (address NOT in allowedSwapper[pool]) is the attacker.

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInput(pool, ..., recipient=Alice, ...).
  2. Router calls pool.swap(recipient=Alice, sender=router, ...).
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Hook returns selector; swap proceeds.
  6. Alice receives token output despite never being allowlisted.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; Alice bypasses the curated allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/interfaces/extensions/ISwapAllowlistExtension.sol (L1-19)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title ISwapAllowlistExtension
/// @notice Per-pool swap allowlist admin and read API.
interface ISwapAllowlistExtension {
  event AllowedToSwapSet(address indexed pool, address indexed swapper, bool allowed);
  event AllowAllSwappersSet(address indexed pool, bool allowed);

  function allowedSwapper(address pool, address swapper) external view returns (bool);

  function allowAllSwappers(address pool) external view returns (bool);

  function setAllowedToSwap(address pool, address swapper, bool allowed) external;

  function setAllowAllSwappers(address pool, bool allowed) external;

  function isAllowedToSwap(address pool, address swapper) external view returns (bool);
}
```
