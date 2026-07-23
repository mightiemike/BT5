### Title
`SwapAllowlistExtension` gates the router address instead of the end user, making per-user swap allowlists bypassable through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct — used as the namespace key) and `sender` is the argument the pool forwards, which is `msg.sender` of the pool's own `swap` call. The `IMetricOmmPoolActions` documentation confirms this: *"Swap allowlist rejected `msg.sender`"*. [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router is `msg.sender` to the pool. The pool therefore passes the **router address** as `sender` to the extension hook, not the originating user's address. [3](#0-2) 

The pool admin allowlists specific end-user addresses:

```
allowedSwapper[pool][user1] = true
allowedSwapper[pool][user2] = true
```

But when `user1` routes through the router, the extension evaluates `allowedSwapper[pool][router]`, which is `false`. To restore router-mediated access for allowlisted users, the admin must set `allowedSwapper[pool][router] = true`. At that point, **every** address — including non-allowlisted users — can bypass the gate by calling through the router.

This is structurally identical to the H-13 bug: the guard reads from the wrong field (the immediate caller / `sender` argument) rather than the economically relevant actor (the originating user). The `DepositAllowlistExtension` avoids this by checking `owner` (the position owner, not the caller), which remains correct even when the liquidity adder is the `msg.sender`. [4](#0-3) 

---

### Impact Explanation

Any user blocked by a curated pool's swap allowlist can execute swaps by routing through `MetricOmmSimpleRouter`. The pool receives tokens and emits output exactly as if the allowlist did not exist. This breaks the core curation invariant — *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it"* — and constitutes a direct policy bypass with fund-impacting consequences (unauthorized users trade against LP capital in a pool designed to restrict access).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the protocol. Any user aware of the allowlist restriction will naturally attempt the router path. No privileged access, special tokens, or unusual setup is required — the bypass is a single public call.

---

### Recommendation

Pass the originating user's address as `sender` rather than the immediate caller. Two options:

1. **Preferred — caller-supplied origin with router enforcement**: Add an `origin` field to the swap parameters (analogous to how `owner` is explicit in `addLiquidity`). The router sets `origin = msg.sender` before calling the pool; the pool forwards `origin` to the extension. The extension checks `allowedSwapper[pool][origin]`.

2. **Alternative — check `tx.origin`**: Replace `sender` with `tx.origin` inside the extension. This is simpler but incompatible with smart-contract swappers and generally discouraged.

The deposit allowlist's pattern of checking the semantically meaningful actor (`owner`) rather than the immediate caller should be replicated for the swap allowlist.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only permitted swapper
  allowedSwapper[pool][router] not set

Step 1 — confirm direct block:
  bob calls pool.swap(...) directly
  → extension sees sender = bob → NOT in allowlist → reverts NotAllowedToSwap ✓

Step 2 — admin enables router for alice:
  admin sets allowedSwapper[pool][router] = true
  (necessary so alice can use the router)

Step 3 — bypass:
  bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient=bob, ...)
  → pool passes msg.sender (router) as sender to extension
  → extension checks allowedSwapper[pool][router] = true → PASSES
  → bob's swap executes against LP capital despite not being allowlisted
``` [1](#0-0) [5](#0-4)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
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
