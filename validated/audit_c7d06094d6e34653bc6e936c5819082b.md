### Title
Single-Step Ownership Transfer Permanently Bricks Protocol Management — (`File: core/contracts/ContractOwner.sol`, `core/contracts/BaseProxyManager.sol`)

---

### Summary
`ContractOwner` and `BaseProxyManager` both inherit OpenZeppelin's `OwnableUpgradeable`, which implements a **single-step** `transferOwnership`. If the multisig owner calls `transferOwnership` with a wrong address, ownership is immediately and irrecoverably transferred. `ContractOwner` is the central admin contract governing product management, risk parameters, NLP pools, verifier key rotation, fee rates, and insurance. `BaseProxyManager` governs all contract upgrade paths. Permanent loss of ownership in either contract bricks the corresponding protocol management surface with no recovery path.

---

### Finding Description

`ContractOwner` is declared as:

```solidity
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
``` [1](#0-0) 

During initialization, ownership is immediately transferred to the multisig in a single step:

```solidity
__Ownable_init();
transferOwnership(multisig);
``` [2](#0-1) 

`OwnableUpgradeable` exposes `transferOwnership(address newOwner)` as a public function callable by the current owner at any time. It immediately sets `_owner = newOwner` with no pending state and no claim step. If the multisig calls `transferOwnership(wrongAddress)`, the original multisig loses all `onlyOwner` access permanently.

The same pattern exists in `BaseProxyManager`:

```solidity
abstract contract BaseProxyManager is OwnableUpgradeable {
``` [3](#0-2) 

Additionally, `BaseProxyManager` exposes its own single-step role transfer for the `submitter` role:

```solidity
function updateSubmitter(address newSubmitter) external onlyOwner {
    submitter = newSubmitter;
}
``` [4](#0-3) 

If `submitter` is set to a wrong address, `onlySubmitter`-gated functions (`submitImpl`, `refreshCodeHash`) become inaccessible, blocking all future implementation proposals. (Note: `submitter` loss is recoverable by the owner via a second `updateSubmitter` call; the `transferOwnership` loss is not.)

---

### Impact Explanation

**`ContractOwner` ownership loss** permanently blocks:
- `addOrUpdateProducts` — no new markets can be listed [5](#0-4) 
- `assignPubKey` / `deletePubkey` — verifier key rotation is permanently blocked [6](#0-5) 
- `spotUpdateRisk` / `perpUpdateRisk` — risk parameters frozen forever [7](#0-6) 
- `withdrawInsurance`, `depositInsurance`, `setWithdrawPool`, `setSpreads`, `updateTierFeeRates`, `addNlpPool`, `updateNlpPool`, `deleteNlpPool`, `rebalanceXWithdraw`, `dumpFees`, `addEngine` — all permanently inaccessible

**`ProxyManager` ownership loss** permanently blocks:
- `migrateAll` — no contract upgrades ever again [8](#0-7) 
- `updateSubmitter` — submitter role frozen [4](#0-3) 
- `forceMigrateSelf`, `registerRegularProxy`, `refreshProxyManagerHelper` — all permanently inaccessible

**Impact: High** — protocol management and upgrade paths are permanently bricked.

---

### Likelihood Explanation

**Likelihood: Low** — requires the multisig to call `transferOwnership` with an incorrect address (e.g., a typo, a stale address, or a non-multisig EOA). This is the same likelihood class as the external report. Multisig operations are human-coordinated and subject to input error, especially during ownership migrations or key rotations.

---

### Recommendation

Replace `OwnableUpgradeable` with `Ownable2StepUpgradeable` (OpenZeppelin) in `ContractOwner`, `BaseProxyManager`, `Verifier`, `SpotEngine`/`PerpEngine` (via `BaseEngine`), and `Clearinghouse`. The two-step pattern requires the new owner to explicitly call `acceptOwnership()`, ensuring the new owner address is valid and controlled before the transfer completes.

For `updateSubmitter` in `BaseProxyManager`, apply the same two-step pattern: store a `pendingSubmitter` and require the pending address to call an `acceptSubmitter()` function.

---

### Proof of Concept

1. Multisig (current owner of `ContractOwner`) intends to rotate ownership to a new multisig.
2. Multisig calls `ContractOwner.transferOwnership(0xWRONG_ADDRESS)` — e.g., a typo or a decommissioned address.
3. `OwnableUpgradeable._transferOwnership` immediately sets `_owner = 0xWRONG_ADDRESS`.
4. The original multisig is no longer the owner.
5. All `onlyOwner` functions in `ContractOwner` — including `assignPubKey`, `spotUpdateRisk`, `perpUpdateRisk`, `addOrUpdateProducts`, `updateTierFeeRates`, `setWithdrawPool`, `addEngine`, etc. — revert with `"Ownable: caller is not the owner"` for any call from the original multisig.
6. If `0xWRONG_ADDRESS` is an EOA with no known private key, or `address(0)` (blocked by OZ), or a contract with no `transferOwnership` interface, the ownership is permanently lost.
7. Protocol management is permanently bricked with no on-chain recovery path. [1](#0-0) [9](#0-8) [10](#0-9) [4](#0-3)

### Citations

**File:** core/contracts/ContractOwner.sol (L21-21)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
```

**File:** core/contracts/ContractOwner.sol (L57-68)
```text
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

**File:** core/contracts/ContractOwner.sol (L147-150)
```text
    function addOrUpdateProducts(
        uint32[] memory spotIds,
        uint32[] memory perpIds
    ) external onlyOwner {
```

**File:** core/contracts/ContractOwner.sol (L441-451)
```text
    function assignPubKey(
        uint256 i,
        uint256 x,
        uint256 y
    ) public onlyOwner {
        verifier.assignPubKey(i, x, y);
    }

    function deletePubkey(uint256 index) public onlyOwner {
        verifier.deletePubkey(index);
    }
```

**File:** core/contracts/ContractOwner.sol (L453-465)
```text
    function spotUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        spotEngine.updateRisk(productId, riskStore);
    }

    function perpUpdateRisk(
        uint32 productId,
        RiskHelper.RiskStore memory riskStore
    ) external onlyOwner {
        perpEngine.updateRisk(productId, riskStore);
    }
```

**File:** core/contracts/BaseProxyManager.sol (L74-95)
```text
abstract contract BaseProxyManager is OwnableUpgradeable {
    string internal constant CLEARINGHOUSE = "Clearinghouse";
    string internal constant CLEARINGHOUSE_LIQ = "ClearinghouseLiq";
    string internal constant ENDPOINT = "Endpoint";
    string internal constant ENDPOINT_TX = "EndpointTx";

    address public submitter;
    ProxyManagerHelper internal proxyManagerHelper;

    string[] internal contractNames;
    mapping(string => address) public proxies;
    mapping(string => address) public pendingImpls;
    mapping(string => bytes32) public pendingHashes;
    mapping(string => bytes32) public codeHashes;

    modifier onlySubmitter() {
        require(
            msg.sender == submitter,
            "only submitter can submit new impls."
        );
        _;
    }
```

**File:** core/contracts/BaseProxyManager.sol (L181-183)
```text
    function updateSubmitter(address newSubmitter) external onlyOwner {
        submitter = newSubmitter;
    }
```

**File:** core/contracts/BaseProxyManager.sol (L189-201)
```text
    function migrateAll(NewImpl[] calldata newImpls) external onlyOwner {
        for (uint32 i = 0; i < newImpls.length; i++) {
            if (_isEqual(newImpls[i].name, CLEARINGHOUSE_LIQ)) {
                _migrateClearinghouseLiq(newImpls[i]);
            } else if (_isEqual(newImpls[i].name, ENDPOINT_TX)) {
                _migrateEndpointTx(newImpls[i]);
            } else {
                _migrateRegularProxy(newImpls[i]);
            }
            codeHashes[newImpls[i].name] = pendingHashes[newImpls[i].name];
        }
        require(!hasPending(), "still having pending impls to be migrated.");
    }
```
