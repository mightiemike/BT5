### Title
SwapAllowlistExtension Bypass via Router Intermediary: `sender` Checked Is the Router, Not the Actual Swapper — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (a natural configuration to enable router-mediated swaps), every unprivileged user can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol
function _beforeSwap(
    address sender,   // ← pool passes msg.sender of pool.swap() here
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap(...)`. Inside the pool, `msg.sender` is the **router**, not the end user. The pool therefore calls `_beforeSwap(sender = router, ...)`, and the extension evaluates:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][actualUser]
```

The allowlist is keyed on the router's address. If the pool admin allowlists the router — a natural step to enable router-mediated swaps for their allowlisted users — **every unprivileged address** can bypass the per-user gate simply by routing through the public router.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all.
- **Allowlist the router** → all users bypass the allowlist.

There is no configuration that achieves the intended semantics (only allowlisted users may swap, including via the router).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a permissioned pool — only specific counterparties are meant to trade. If the router is allowlisted, any unprivileged user can:

1. Execute swaps on the permissioned pool, extracting value at oracle-anchored prices.
2. Cause unauthorized price impact, moving the pool's bin cursor and affecting LP positions.
3. Drain liquidity from bins that were intended to be accessible only to vetted counterparties.

This breaks the **swap conservation** and **admin-boundary** invariants: an unprivileged path reaches a pool action that the pool admin explicitly intended to restrict.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, production periphery contract.
- A pool admin who wants allowlisted users to be able to use the router (the standard UX path) will naturally allowlist the router address.
- The admin has no on-chain signal that doing so opens the gate to all users; the allowlist UI/admin call looks identical whether the target is a user or the router.
- No special privilege or malicious setup is required from the attacker — calling the public router is sufficient.

---

### Recommendation

The extension must gate the **originating user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the call chain.** Have `MetricOmmSimpleRouter` pass `msg.sender` (the end user) as the `sender` argument to `pool.swap()`, and have the pool forward it faithfully to the extension. This requires the pool's `swap()` signature to accept an explicit `sender` parameter rather than using `msg.sender` internally.

2. **Check `recipient` instead of `sender` for router flows, or use a dedicated allowlist entry for the router that also validates the originating user via `extensionData`.** The extension could decode a signed user address from `extensionData` when `sender` is a known router.

The simplest correct fix is approach (1): the router passes `msg.sender` as the explicit `sender` to the pool, and the pool forwards it to extensions, so `allowedSwapper[pool][actualUser]` is evaluated.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as EXTENSION_1,
         beforeSwap order = [1]
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
    // admin intends to enable router-mediated swaps for allowlisted users
    // but this allowlists the router itself, not individual users

Attack:
  attacker = address not in allowedSwapper[pool]
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      zeroForOne: true,
      amountIn: X,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...)   // msg.sender in pool = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ (passes)
      → swap executes, attacker receives tokens

Result:
  Attacker, who is NOT in the per-user allowlist, successfully swaps on the
  permissioned pool. The swap allowlist guard is fully bypassed.
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
