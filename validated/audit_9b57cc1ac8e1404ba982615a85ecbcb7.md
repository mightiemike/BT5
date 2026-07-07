### Title
Irrevocable Deployer Privilege Persists After Ownership Transfer, Enabling Forced Product Delisting - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.sol` stores the deployer EOA address permanently at initialization and exposes an `onlyDeployer` modifier that gates critical protocol functions — including `delistProduct`. Ownership is explicitly transferred to a multisig during `initialize()`, but the `deployer` address is never revocable. There is no function to change or nullify it. A compromised or malicious deployer EOA retains the ability to forcibly delist perpetual products and close all user positions indefinitely after governance has been handed to the multisig.

---

### Finding Description

In `ContractOwner.initialize()`, ownership is transferred to a multisig on line 60, but the deployer EOA is stored separately on line 61: [1](#0-0) 

The `onlyDeployer` modifier checks only `msg.sender == deployer`: [2](#0-1) 

The following functions are gated exclusively by `onlyDeployer`, not `onlyOwner`:

- `delistProduct` (line 365) — submits a `DelistProduct` slow-mode transaction directly to the Endpoint
- `submitSpotAddOrUpdateProductCall` (line 98) — queues spot product additions
- `submitPerpAddOrUpdateProductCall` (line 122) — queues perp product additions
- `clearSpotAddOrUpdateProductCalls` (line 139) — deletes the pending spot queue
- `clearPerpAddOrUpdateProductCalls` (line 143) — deletes the pending perp queue [3](#0-2) 

The `delistProduct` call flows through `Endpoint` → `EndpointTx.processSlowModeTransactionImpl` → `clearinghouse.delistProduct()`: [4](#0-3) 

In `Clearinghouse.delistProduct()`, all positions in the targeted perpetual product are forcibly closed at the provided price (validated against the oracle at execution time): [5](#0-4) 

There is **no setter for `deployer`** anywhere in the contract. The multisig owner has no mechanism to revoke the deployer's capabilities.

---

### Impact Explanation

A compromised deployer EOA can call `ContractOwner.delistProduct()` to forcibly close all open perpetual positions across any listed product. The Clearinghouse zeroes out every subaccount's `amount` and applies a `vQuoteDelta` at the current oracle price. Users with leveraged positions are force-settled without consent, causing direct financial loss. The deployer can also sabotage governance-approved product additions by calling `clearSpotAddOrUpdateProductCalls` or `clearPerpAddOrUpdateProductCalls` immediately before the multisig executes `addOrUpdateProducts`, blocking new market listings. [6](#0-5) 

---

### Likelihood Explanation

The protocol explicitly transfers ownership to a multisig at initialization, demonstrating intent to reduce single-key risk. However, the deployer EOA is a single private key — inherently weaker than a multisig. If the deployer key is ever leaked, rotated insecurely, or held by a team member who becomes malicious, the deployer's `onlyDeployer` privileges cannot be stripped by the multisig. The likelihood is low in normal operation but non-negligible over a long protocol lifetime, and the impact is severe enough to warrant Medium severity. [7](#0-6) 

---

### Recommendation

1. Add a `setDeployer(address newDeployer)` function gated by `onlyOwner` so the multisig can rotate or nullify the deployer address (e.g., set it to `address(0)`).
2. Alternatively, migrate `delistProduct` to `onlyOwner` so the multisig controls it directly, removing the need for a separate deployer role post-deployment.
3. At minimum, document that the deployer EOA must be treated as a privileged key indefinitely and should be secured to the same standard as the multisig.

---

### Proof of Concept

1. Protocol is deployed. `ContractOwner.initialize()` is called: ownership transfers to the multisig, but `deployer` is set to the deployer EOA.
2. The multisig now governs the protocol. Users open leveraged perpetual positions.
3. The deployer EOA is compromised (single key, lower security bar than a multisig).
4. The attacker calls `ContractOwner.delistProduct([productId], [currentOraclePrice], [allSubaccounts])` directly from the deployer EOA.
5. This submits a `DelistProduct` slow-mode transaction. The Endpoint processes it, calling `Clearinghouse.delistProduct()`.
6. All user positions in the targeted product are forcibly closed at the oracle price. Users with open leveraged longs or shorts are settled without consent, suffering financial loss.
7. The multisig has no way to prevent this — it cannot revoke the deployer's `onlyDeployer` access. [8](#0-7) [2](#0-1) [3](#0-2) [5](#0-4)

### Citations

**File:** core/contracts/ContractOwner.sol (L26-27)
```text
    address internal deployer;
    SpotEngine internal spotEngine;
```

**File:** core/contracts/ContractOwner.sol (L48-68)
```text
    function initialize(
        address multisig,
        address _deployer,
        address _spotEngine,
        address _perpEngine,
        address _endpoint,
        address _clearinghouse,
        address _verifier,
        address payable _wrappedNative
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
        spotEngine = SpotEngine(_spotEngine);
        perpEngine = PerpEngine(_perpEngine);
        endpoint = Endpoint(_endpoint);
        clearinghouse = IClearinghouse(_clearinghouse);
        verifier = Verifier(_verifier);
        wrappedNative = _wrappedNative;
    }
```

**File:** core/contracts/ContractOwner.sol (L70-73)
```text
    modifier onlyDeployer() {
        require(msg.sender == deployer, "sender must be deployer");
        _;
    }
```

**File:** core/contracts/ContractOwner.sol (L361-380)
```text
    function delistProduct(
        uint32[] calldata productIds,
        int128[] calldata pricesX18,
        bytes32[] calldata subaccounts
    ) external onlyDeployer {
        if (productIds.length != pricesX18.length) {
            revert InvalidInput();
        }
        for (uint256 i = 0; i < productIds.length; i++) {
            IEndpoint.DelistProduct memory _txn = IEndpoint.DelistProduct(
                productIds[i],
                pricesX18[i],
                subaccounts
            );
            _submitSlowModeTransaction(
                IEndpoint.TransactionType.DelistProduct,
                abi.encode(_txn)
            );
        }
    }
```

**File:** core/contracts/EndpointTx.sol (L242-243)
```text
        } else if (txType == IEndpoint.TransactionType.DelistProduct) {
            clearinghouse.delistProduct(transaction);
```

**File:** core/contracts/Clearinghouse.sol (L294-325)
```text
    function delistProduct(bytes calldata transaction) external onlyEndpoint {
        IEndpoint.DelistProduct memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.DelistProduct)
        );
        // only perp can be delisted
        require(
            productToEngine[txn.productId] == _perpEngine(),
            ERR_INVALID_PRODUCT
        );
        require(txn.priceX18 == _getPriceX18(txn.productId), ERR_INVALID_PRICE);
        IPerpEngine perpEngine = _perpEngine();
        for (uint256 i = 0; i < txn.subaccounts.length; i++) {
            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                txn.productId,
                txn.subaccounts[i]
            );
            int128 baseDelta = -balance.amount;
            int128 quoteDelta = -baseDelta.mul(txn.priceX18);
            perpEngine.updateBalance(
                txn.productId,
                txn.subaccounts[i],
                baseDelta,
                quoteDelta
            );
            if (RiskHelper.isIsolatedSubaccount(txn.subaccounts[i])) {
                IOffchainExchange(
                    IEndpoint(getEndpoint()).getOffchainExchange()
                ).tryCloseIsolatedSubaccount(txn.subaccounts[i]);
            }
        }
    }
```
