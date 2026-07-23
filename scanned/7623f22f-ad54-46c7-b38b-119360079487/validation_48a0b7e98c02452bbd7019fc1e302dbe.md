### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist by nominating an allowlisted address as position owner — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently discards the `sender` argument (the actual depositor/payer) and checks only `owner` (the LP position recipient). Because `MetricOmmPool.addLiquidity` accepts a fully caller-supplied `owner` with no requirement that `msg.sender == owner`, any unprivileged, non-allowlisted caller can bypass the deposit guard by nominating an allowlisted address as `owner`. The non-allowlisted caller pays the tokens; the allowlisted address receives LP shares it never requested.

---

### Finding Description

**`DepositAllowlistExtension.beforeAddLiquidity`** (line 32–42):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The first positional parameter — `sender`, the address that will be charged tokens via the pool callback — is unnamed and discarded. Only `owner` is checked.

**`MetricOmmPool.addLiquidity`** (lines 182–196):

```solidity
function addLiquidity(
    address owner,          // ← fully caller-supplied, no msg.sender == owner check
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    ...
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);   // sender = msg.sender
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...        // LP shares → owner
    );
    ...
}
```

There is no `require(msg.sender == owner)` guard. The pool passes `msg.sender` as `sender` to the extension, but the extension ignores it.

**Attack path:**

1. Pool is configured with `DepositAllowlistExtension`. Alice is allowlisted; Bob is not.
2. Bob calls `pool.addLiquidity(owner = Alice, salt = X, deltas = ..., callbackData = ..., extensionData = ...)`.
3. The extension evaluates `allowedDepositor[pool][Alice]` → `true` → passes without reverting.
4. `LiquidityLib.addLiquidity` mints LP shares to Alice and calls the liquidity callback on Bob (`msg.sender`) to pull tokens.
5. Bob has deposited into a pool he is not permitted to access; Alice holds LP shares she never requested.

Compare with `removeLiquidity` (line 206): `if (msg.sender != owner) revert NotPositionOwner();` — withdrawal correctly enforces identity, but deposit does not.

---

### Impact Explanation

- **Allowlist bypass (direct):** Any non-allowlisted address can deposit tokens into a curated pool by supplying an allowlisted `owner`. The pool receives funds from actors the admin explicitly excluded, defeating KYC, regulatory, or curation controls.
- **Forced LP exposure (griefing with fund risk):** Alice is assigned LP shares in a pool she never chose to enter. Until she detects and removes the position, she bears full impermanent-loss and oracle-price-move risk on those shares. If the pool's oracle price moves adversely between the forced deposit and Alice's withdrawal, Alice suffers a direct loss of principal.
- **Allowlist intent fully nullified:** Because `owner` is caller-supplied and unconstrained, the guard provides zero protection against the actual depositing actor.

---

### Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity` directly.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor` mapping).
- Cost to the attacker is the deposited tokens, but the minimum viable deposit can be dust-sized if the pool accepts it (shares must be non-zero, analogous to the 1-wei attack in H-03).
- The attack is repeatable in every block, continuously refreshing Alice's unwanted exposure.

---

### Recommendation

**Option A (preferred):** Change `beforeAddLiquidity` to check `sender` (the actual payer) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

**Option B:** Add `require(msg.sender == owner)` in `MetricOmmPool.addLiquidity` to prevent depositing on behalf of others (mirrors the `removeLiquidity` pattern).

**Option C:** Check both `sender` and `owner` if the intent is to gate both the payer and the position recipient.

---

### Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; Alice allowlisted, Bob not.
depositExtension.setAllowedToDeposit(address(pool), alice, true);

// Bob (non-allowlisted) deposits on behalf of Alice.
vm.startPrank(bob);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// Extension checks allowedDepositor[pool][alice] == true → passes.
// Callback pulls tokens from bob; LP shares minted to alice.
pool.addLiquidity(
    alice,                          // owner = allowlisted victim
    uint80(0),                      // salt
    LiquidityDelta({binIdxs: bins, shares: amounts}),
    callbackData,
    ""
);
vm.stopPrank();

// Alice now holds LP shares she never requested.
// Bob successfully deposited into a pool he is not permitted to access.
assertGt(pool.positionShares(alice, 0, bins[0]), 0);
``` [1](#0-0) 
<cite repo="Thankgoddavid56/2026-07-metric-dev-oyakhil-main--

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
