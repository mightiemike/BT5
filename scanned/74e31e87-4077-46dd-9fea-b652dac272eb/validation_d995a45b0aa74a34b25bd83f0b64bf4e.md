### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to grief an LP's full-withdrawal `removeLiquidity` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. Its `beforeAddLiquidity` hook receives the actual caller (`sender`) as its first argument and the position beneficiary (`owner`) as its second. The guard checks `owner` instead of `sender`, so any unprivileged address can call `addLiquidity(owner = allowlisted_LP, ...)`, pass the allowlist, and inject a dust share-count into the victim LP's position. A subsequent `removeLiquidity` that tries to burn the LP's entire original share balance then leaves a residual below `MINIMAL_MINTABLE_LIQUIDITY` and reverts, permanently blocking full withdrawal.

---

### Finding Description

**Step 1 – Wrong address checked in the allowlist hook.** [1](#0-0) 

The NatSpec says "Gates `addLiquidity` by depositor address". The hook signature is `beforeAddLiquidity(address /*sender*/, address owner, …)`. When the pool calls this hook it passes `msg.sender` (the original `addLiquidity` caller) as the first argument and the position beneficiary as `owner`. The guard reads:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert NotAllowedToDeposit();
}
```

Here `msg.sender` is the **pool** (the hook is called by the pool). So the check is `allowedDepositor[pool][owner]` — it verifies whether the **beneficiary** is allowlisted, not whether the **actual caller** is allowlisted. The first (unnamed) `sender` parameter is silently discarded.

**Step 2 – Any caller can add shares to any owner's position.** [2](#0-1) 

`addLiquidity` accepts an arbitrary `owner` address; `msg.sender` need not equal `owner`. The pool calls `_beforeAddLiquidity(msg.sender, owner, …)` and then `LiquidityLib.addLiquidity(…, owner, …)`. Because the allowlist only validates `owner`, an attacker whose address is **not** in the allowlist can pass `owner = Alice` (who is allowlisted) and the hook approves the call.

**Step 3 – Dust injection causes `removeLiquidity` to revert.** [3](#0-2) 

`removeLiquidity` enforces:

```solidity
uint256 newUserShares = userShares - sharesToRemove;
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```

If Alice holds `N` shares and submits `removeLiquidity(shares = N)`, an attacker who front-runs with `addLiquidity(owner = Alice, shares = 1)` leaves Alice with `N + 1` shares. Alice's transaction then computes `newUserShares = 1`, which satisfies `> 0 && < MINIMAL_MINTABLE_LIQUIDITY`, and reverts. The attacker pays only the cost of 1 share (plus gas) per grief.

---

### Impact Explanation

- **Allowlist bypass**: Any unprivileged address can deposit into a pool that is supposed to be restricted to approved depositors, violating the pool admin's access-control intent.
- **Broken core withdraw flow**: An LP who wants to fully exit a position can be permanently blocked by a cheap, repeatable front-run. Every retry can be griefed again for the cost of 1 share. This is a broken core pool functionality causing an unusable withdraw flow, meeting the contest-relevant impact gate.

---

### Likelihood Explanation

- `addLiquidity` is a public, permissionless entry point; no special role is required.
- The attacker only needs to hold a trivially small token amount (1 share worth of collateral).
- The attack is repeatable: every time Alice retries with the correct new share count, the attacker can front-run again.
- The allowlist is supposed to be the guard against exactly this class of third-party interference; its misconfiguration removes the only intended protection.

---

### Recommendation

Fix `DepositAllowlistExtension.beforeAddLiquidity` to check the **actual caller** (first parameter) rather than the beneficiary:

```solidity
function beforeAddLiquidity(
    address sender,   // ← name and use this
    address,          // owner — not the depositor
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Additionally, consider whether `addLiquidity` should require `msg.sender == owner` when no explicit delegation is intended, or add a separate delegation allowlist, to close the underlying grieving vector at the pool level.

---

### Proof of Concept

```
Setup
─────
• Pool configured with DepositAllowlistExtension.
• Alice (allowlisted) holds 10 000 shares in bin 4, salt = 0.
• Bob (NOT allowlisted) holds a tiny token balance.

Attack
──────
1. Alice broadcasts: removeLiquidity(owner=Alice, salt=0, shares=[10000])

2. Bob sees Alice's tx in the mempool and front-runs:
   addLiquidity(owner=Alice, salt=0, binIdxs=[4], shares=[1], …)

   Hook check: allowedDepositor[pool][Alice] == true  ✓  (Alice is allowlisted)
   Bob's address is never checked.
   Alice's positionBinShares[key] becomes 10 001.

3. Alice's tx executes:
   newUserShares = 10 001 − 10 000 = 1
   1 > 0 && 1 < MINIMAL_MINTABLE_LIQUIDITY  →  revert MinimalLiquidity(1, 1000)

4. Alice's full withdrawal is blocked. Bob repeats for every retry.
   Cost per grief: 1 share of token collateral + gas.
``` [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L196-202)
```text
          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```
