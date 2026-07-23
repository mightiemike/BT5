### Title
`DepositAllowlistExtension` checks LP position `owner` instead of actual depositor `sender`, allowing complete allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook is supposed to gate deposits by depositor address. However, it checks the `owner` parameter (the LP position recipient, a caller-controlled input) instead of the `sender` parameter (the actual depositor, i.e., `msg.sender` of the pool call). Any unprivileged address can bypass the allowlist by calling `addLiquidity` with `owner` set to any allowlisted address.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool invokes the extension hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the actual depositor (who must supply tokens via the swap callback), while `owner` is a free parameter the caller supplies to designate the LP position recipient. Both are forwarded to the extension as `(sender, owner, ...)`: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives the signature `(address sender, address owner, ...)` but silently discards `sender` (the first parameter is unnamed/ignored) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

Because `owner` is a caller-supplied argument with no on-chain constraint, any address can pass the allowlist check by setting `owner` to any address that the pool admin has previously allowlisted.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity. With this bug the restriction is completely ineffective: any address — regardless of allowlist status — can deposit tokens into a restricted pool. The pool admin's configured security boundary is bypassed by an unprivileged path, satisfying the "Admin-boundary break" impact category. Downstream consequences include:

- Unrestricted actors depositing into pools intended for KYC/whitelisted LPs only.
- Forced LP positions onto allowlisted addresses (griefing), since the position is credited to `owner`, not the actual depositor.
- Potential manipulation of pool bin balances and share accounting in pools that combine `DepositAllowlistExtension` with `OracleValueStopLossExtension` watermarks.

---

### Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with `owner` set to any known allowlisted address. No special privileges, flash loans, or oracle manipulation are needed. The allowlisted address is discoverable from on-chain events (`AllowedToDepositSet`). Likelihood is **High**.

---

### Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP position recipient):

```solidity
// BEFORE (broken — checks LP recipient, not depositor)
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}

// AFTER (correct — checks actual depositor)
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` in its `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. `bob` (not on allowlist) calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — alice IS on the allowlist
       salt,
       deltas,
       callbackData,   // bob's contract supplies tokens here
       extensionData
   );
   ```
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → hook passes.
5. `bob`'s callback supplies the tokens; `alice` receives the LP shares.
6. `bob` has deposited into a pool he is explicitly barred from, with zero revert.

The `sender` parameter — `bob`'s address — is never read by the extension. [3](#0-2) [4](#0-3)

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
