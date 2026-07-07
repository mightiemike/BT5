### Title
Silent Liquidation Bypass via Missing `address(0)` Validation on `_clearinghouseLiq` in `Clearinghouse.initialize()` — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.initialize()` accepts `_clearinghouseLiq` without validating it is not `address(0)`. If initialized with `address(0)`, every subsequent call to `liquidateSubaccount()` silently succeeds via a delegatecall to the zero address, consuming the liquidator's fee and nonce while performing no actual liquidation. Unhealthy subaccounts persist indefinitely, breaking the protocol's solvency invariant.

---

### Finding Description

`Clearinghouse.initialize()` stores `_clearinghouseLiq` directly without any zero-address guard:

```solidity
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,   // ← no require(_clearinghouseLiq != address(0))
    uint256 _spreads,
    address _withdrawPool
) external initializer {
    ...
    clearinghouseLiq = _clearinghouseLiq;
    ...
}
``` [1](#0-0) 

`liquidateSubaccount()` then unconditionally delegatecalls into `clearinghouseLiq`:

```solidity
(bool success, bytes memory result) = clearinghouseLiq.delegatecall(
    liquidateSubaccountCall
);
require(success, string(result));
``` [2](#0-1) 

In the EVM, a low-level `delegatecall` to `address(0)` (an account with no code) returns `(true, "")`. The `require(success, ...)` check passes. The function returns normally — no liquidation logic executes.

The liquidation is submitted through `EndpointTx.processTransactionImpl`, which charges the liquidator a `LIQUIDATION_FEE` before calling `clearinghouse.liquidateSubaccount(signedTx.tx)`:

```solidity
if (signedTx.tx.productId != type(uint32).max) {
    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
}
clearinghouse.liquidateSubaccount(signedTx.tx);
``` [3](#0-2) 

The nonce is also consumed by `validateSignedTx`. The liquidator pays a fee and burns a nonce, but the unhealthy subaccount is never touched.

There is a post-deployment upgrade path via `upgradeClearinghouseLiq`, but it requires the `ProxyManagerHelper` to call it, and any liquidations attempted before the fix are permanently lost. [4](#0-3) 

---

### Impact Explanation

- **Broken invariant**: The protocol's core solvency guarantee — that subaccounts below maintenance health are liquidated — is silently voided. Unhealthy subaccounts accumulate unbounded losses against the insurance fund and other depositors.
- **Liquidator asset loss**: Every liquidator who submits a `LiquidateSubaccount` transaction loses `LIQUIDATION_FEE` in quote tokens and consumes a nonce, receiving nothing in return.
- **Protocol insolvency risk**: Without functioning liquidations, bad debt socializes across all depositors via `socializeSubaccount`, corrupting the `cumulativeDepositsMultiplierX18` for all spot products.

---

### Likelihood Explanation

Deployment of upgradeable proxy systems with multiple constructor/initializer arguments is a known source of misconfiguration. The `_clearinghouseLiq` address is a separate implementation contract (`ClearinghouseLiq`) that must be deployed before `Clearinghouse` is initialized. A deployment script error, wrong argument ordering, or a placeholder `address(0)` left in a config file is a realistic path to this state. The original Alluvial report was rated Medium Risk for the same class of issue.

---

### Recommendation

Add a zero-address guard in `Clearinghouse.initialize()`:

```solidity
require(_clearinghouseLiq != address(0), "clearinghouseLiq is zero");
```

Similarly, add guards for `_endpoint`, `_quote`, and `_withdrawPool` in the same initializer, and for `_clearinghouse` and `_verifier` in `BaseWithdrawPool._initialize()`. [5](#0-4) 

---

### Proof of Concept

1. Deploy `Clearinghouse` proxy and call `initialize(endpoint, quote, address(0), spreads, withdrawPool)`.
2. Protocol operates normally for deposits, trades, and price updates.
3. A subaccount's maintenance health drops below zero (e.g., due to adverse price movement).
4. A liquidator constructs a valid `LiquidateSubaccount` signed transaction and submits it via `Endpoint.submitTransactionsChecked`.
5. `EndpointTx.processTransactionImpl` charges `LIQUIDATION_FEE` from the liquidator's balance and calls `clearinghouse.liquidateSubaccount(signedTx.tx)`.
6. `Clearinghouse.liquidateSubaccount` executes `address(0).delegatecall(...)` → returns `(true, "")` → `require(true)` passes.
7. The liquidator's fee is deducted, their nonce is incremented, but the unhealthy subaccount's position is unchanged.
8. The subaccount's health remains negative. Repeat indefinitely — no liquidation ever succeeds. [6](#0-5)

### Citations

**File:** core/contracts/Clearinghouse.sol (L25-40)
```text
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external initializer {
        __Ownable_init();
        setEndpoint(_endpoint);
        quote = _quote;
        clearinghouse = address(this);
        clearinghouseLiq = _clearinghouseLiq;
        spreads = _spreads;
        withdrawPool = _withdrawPool;
        emit ClearinghouseInitialized(_endpoint, _quote);
    }
```

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```

**File:** core/contracts/Clearinghouse.sol (L677-684)
```text
    function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        clearinghouseLiq = _clearinghouseLiq;
    }
```

**File:** core/contracts/EndpointTx.sol (L408-412)
```text
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
            }
            clearinghouse.liquidateSubaccount(signedTx.tx);
```

**File:** core/contracts/BaseWithdrawPool.sol (L23-30)
```text
    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }
```
