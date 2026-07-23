Looking at the `SwapAllowlistExtension` and how `sender` is bound in the hook call chain, there is a clear analog to the external report's "wrong interface assumption" class of bug.

---

### Title
`SwapAllowlistExtension` checks `sender` (router/caller) instead of `recipient` (actual beneficiary), allowing any user to bypass the swap allowlist via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its allowlist against the `sender` parameter, which is the `msg.sender` of `pool.swap()` — i.e., the router contract — not the `recipient` who receives the output tokens and economically benefits from the swap. Any user who is not on the allowlist can bypass the guard by routing through `MetricOmmSimpleRouter` (or any other allowlisted intermediary), because the router's address passes the check while the actual user's address is never inspected.

---

### Finding Description

`ExtensionCalling._beforeSwap` encodes two distinct address fields into the hook payload:

```
sender    = msg.sender of pool.swap()   → the router
recipient = the address receiving tokens → the actual user
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives both fields but only inspects the first one (`sender`), silently discarding `recipient`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  ...
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router becomes `msg.sender` of `pool.swap()`, so `sender` = router address. If the pool admin has allowlisted the router (the normal operational setup), the check passes unconditionally for every user, regardless of whether that user is on the allowlist.

The `DepositAllowlistExtension` does not share this flaw — it correctly checks `owner` (the LP position beneficiary, second parameter), which is the address the admin intends to gate: [3](#0-2) 

The asymmetry confirms the swap extension is checking the wrong field.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to be a private or permissioned trading venue. The allowlist is the sole enforcement mechanism. Because the check targets the router rather than the actual user, the guard is entirely ineffective for router-mediated swaps. Any non-allowlisted address can trade in the pool, defeating the LP's access-control intent and potentially exposing LP capital to counterparties the admin explicitly excluded.

---

### Likelihood Explanation

Exploitation requires no special privileges. The standard `MetricOmmSimpleRouter` is the canonical entry point for swaps. Any user who calls the router against a pool with this extension active bypasses the guard automatically. No admin action, no flash loan, no callback — just a normal router swap.

---

### Recommendation

Change the allowlist check in `beforeSwap` to inspect `recipient` (the second parameter) instead of `sender`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

This mirrors the correct pattern used by `DepositAllowlistExtension`, which gates the position beneficiary (`owner`) rather than the caller.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Call `setAllowedToSwap(pool, router, true)` — the normal operational step to allow the router to interact with the pool.
3. Do **not** allowlist `userB`.
4. `userB` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ..., recipient=userB, ...)`.
5. The pool calls `_beforeSwap(sender=router, recipient=userB, ...)`.
6. The extension checks `allowedSwapper[pool][router]` → `true` → no revert.
7. `userB` successfully swaps in a pool they were explicitly excluded from. [4](#0-3)

### Citations

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
