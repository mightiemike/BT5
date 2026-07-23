### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook silently drops the `sender` argument and checks `owner` (the position recipient) against the allowlist instead. Because `MetricOmmPool.addLiquidity` permits `msg.sender ≠ owner`, any unprivileged caller can bypass the allowlist by supplying an allowlisted address as `owner`, paying the tokens themselves via the swap callback, and having the position credited to that address.

---

### Finding Description

`MetricOmmPool.addLiquidity` separates the caller (`msg.sender`, forwarded as `sender`) from the position recipient (`owner`):

```solidity
// MetricOmmPool.sol – addLiquidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
(amount0Added, amount1Added) = LiquidityLib.addLiquidity(
    _liquidityContext(), owner, salt, deltas, callbackData, ...
);
``` [1](#0-0) 

There is no `require(msg.sender == owner)` guard in `addLiquidity` (unlike `removeLiquidity`, which enforces `msg.sender == owner`): [2](#0-1) 

The extension hook receives `(sender, owner, ...)` but discards `sender` entirely (unnamed first parameter) and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

Because `owner` is a caller-controlled parameter with no on-chain binding to `msg.sender`, any address can pass the allowlist check by simply supplying an allowlisted address as `owner`. The actual token payment flows through the callback on `msg.sender` (the unauthorized depositor), and the resulting position shares are credited to `owner`.

Compare with `SwapAllowlistExtension`, which correctly checks the first argument (`sender` = the actual caller of `swap`): [4](#0-3) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity to a pool. With this bug the guard is completely ineffective: any unprivileged address can deposit into a restricted pool by naming any known allowlisted address as `owner`. Consequences include:

- **Allowlist bypass**: unauthorized parties inject liquidity into pools intended to be permissioned (e.g., institutional or KYC-gated pools).
- **Pool-state manipulation**: an attacker can shift bin balances and `curPosInBin` in ways the pool admin did not authorize, affecting swap prices and LP returns for existing depositors.
- **Broken core pool functionality**: the security invariant that only allowlisted depositors can add liquidity is violated, which is a broken core guarantee under the contest's impact gate ("allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through … owner/salt separation").

---

### Likelihood Explanation

**High.** The bypass requires no special privilege, no flash loan, and no complex setup. The attacker only needs to know one allowlisted address (which may be publicly visible on-chain via past `AllowedToDepositSet` events). The call is a single `addLiquidity` invocation with `owner` set to that address.

---

### Recommendation

Check `sender` (the actual caller) instead of `owner` (the position recipient):

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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is allowlisted; `bob` is not.
3. `bob` (unauthorized) calls:
   ```solidity
   pool.addLiquidity(
       alice,   // owner — allowlisted, passes the check
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice] == true` → no revert.
5. `LiquidityLib.addLiquidity` calls back into `bob` (msg.sender) to collect tokens; position shares are minted to `alice`.
6. `bob` has successfully deposited into the restricted pool, bypassing the allowlist entirely. [3](#0-2) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-40)
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
```
