### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unauthorized depositor to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (the actual caller of `addLiquidity`) and checks the caller-controlled `owner` parameter instead. Because `owner` is a free argument in `pool.addLiquidity(address owner, …)`, any address — regardless of allowlist status — can bypass the guard by supplying an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the actual depositor (the address that will pay tokens via the liquidity callback); `owner` is the address that will hold the resulting LP position and is freely chosen by the caller.

Inside `DepositAllowlistExtension`, the first parameter (`sender`) is discarded with an unnamed placeholder, and only `owner` is checked:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Because `owner` is a caller-supplied argument, any unauthorized address can pass the guard by setting `owner` to any address that is already on the allowlist. The unauthorized address pays the tokens (via the add-liquidity callback), and the allowlisted address receives the LP shares — but the allowlist restriction is completely defeated.

This is structurally identical to the EigenLayer M-01 pattern: the enforcement mechanism (slashing / allowlist check) operates on a value that has been temporarily zeroed or substituted (shares reduced to 0 / `owner` swapped for `sender`), so the guard passes vacuously, and the actor retains or obtains a position they should not have.

Contrast with `SwapAllowlistExtension`, which correctly checks `sender` (the actual caller of `swap`):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [3](#0-2) 

The asymmetry between the two sibling extensions confirms that checking `owner` in `DepositAllowlistExtension` is unintentional.

---

### Impact Explanation

The deposit allowlist is an admin-configured access-control boundary. Its stated purpose is to gate `addLiquidity` by depositor address. Because the check is on the wrong identity, the boundary is bypassed by any unprivileged address with zero special access. This is an **admin-boundary break**: a security control set by the pool admin is circumvented through a normal, publicly accessible call path. Unauthorized parties can inject liquidity into a restricted pool, defeating KYC/compliance restrictions, altering the pool's liquidity distribution, and potentially affecting LP value and swap outcomes in ways the pool admin explicitly sought to prevent.

---

### Likelihood Explanation

The bypass requires a single call to `pool.addLiquidity` with `owner` set to any allowlisted address. No privileges, flash loans, or multi-step setup are needed. The allowlisted address is discoverable on-chain via the `allowedDepositor` mapping. Likelihood is high.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner`, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` extension.
2. Pool admin allowlists address **A**: `setAllowedToDeposit(pool, A, true)`.
3. Unauthorized address **B** (not on the allowlist) calls:
   ```solidity
   pool.addLiquidity(
       /* owner = */ A,      // allowlisted — passes the guard
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. Pool calls `_beforeAddLiquidity(msg.sender=B, owner=A, …)`.
5. Extension evaluates `allowedDepositor[pool][A]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` creates the position keyed to `(A, salt)` and calls back to **B** to collect tokens.
7. **B** pays the tokens; **A** receives the LP shares.
8. **B** has successfully deposited into a restricted pool without being allowlisted. The deposit allowlist is fully bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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
