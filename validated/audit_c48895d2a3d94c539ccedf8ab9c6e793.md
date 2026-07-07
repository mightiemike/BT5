### Title
`ContractOwner::assignPubKey` and `ContractOwner::deletePubkey` Always Revert Due to Wrong Access Control on `Verifier` - (File: `core/contracts/ContractOwner.sol`, `core/contracts/Verifier.sol`)

---

### Summary

`ContractOwner` is the intended multisig-controlled management interface for the Nado protocol. It exposes `assignPubKey` and `deletePubkey` to manage sequencer signing keys on the `Verifier` contract. However, both target functions in `Verifier` are protected by `onlyOwner`, and `ContractOwner` is never the owner of `Verifier`. Every call through this path reverts unconditionally.

---

### Finding Description

`ContractOwner.assignPubKey` and `ContractOwner.deletePubkey` forward calls directly to `verifier.assignPubKey(...)` and `verifier.deletePubkey(...)`: [1](#0-0) 

In `Verifier.sol`, both target functions are gated by `onlyOwner`: [2](#0-1) [3](#0-2) 

`Verifier` is an upgradeable contract that sets its owner via `__Ownable_init()` in its `initialize()` function, which assigns ownership to `msg.sender` at initialization time: [4](#0-3) 

`ContractOwner.initialize()` only stores a reference to the already-deployed `Verifier` instance — it does not acquire ownership of it: [5](#0-4) 

Because `ContractOwner` is never the owner of `Verifier`, every call to `verifier.assignPubKey(...)` or `verifier.deletePubkey(...)` from `ContractOwner` will revert with `OwnableUnauthorizedAccount`. There is no code path that transfers `Verifier` ownership to `ContractOwner`.

---

### Impact Explanation

`assignPubKey` and `deletePubkey` are the only on-chain mechanism to rotate or revoke sequencer signing keys. The `Verifier` uses these keys to validate all sequencer-submitted transactions via `requireValidTxSignatures` and `requireValidSignature`: [6](#0-5) 

Since `ContractOwner` is the designated multisig management interface, the inability to call these functions means:

- Compromised sequencer keys cannot be revoked on-chain through the intended path.
- New sequencer keys cannot be added through the intended path.
- The protocol's key rotation mechanism is permanently broken at the `ContractOwner` layer.

This is a cross-contract desynchronization: `ContractOwner` exposes management functions that are structurally unreachable due to the access control mismatch.

---

### Likelihood Explanation

This will fail deterministically on the first attempt by the multisig to call `ContractOwner.assignPubKey` or `ContractOwner.deletePubkey`. No special conditions are required — the revert is guaranteed by the ownership mismatch baked in at deployment.

---

### Recommendation

Either transfer ownership of `Verifier` to `ContractOwner` after deployment, or add a dedicated access role (e.g., `onlyContractOwner`) to `Verifier.assignPubKey` and `Verifier.deletePubkey` that permits `ContractOwner` to call them:

```diff
// Verifier.sol
- function assignPubKey(uint256 i, uint256 x, uint256 y) public onlyOwner {
+ function assignPubKey(uint256 i, uint256 x, uint256 y) public onlyOwnerOrContractOwner {
      _assignPubkey(i, x, y);
  }

- function deletePubkey(uint256 index) public onlyOwner {
+ function deletePubkey(uint256 index) public onlyOwnerOrContractOwner {
      ...
  }
```

Alternatively, during `ContractOwner.initialize()`, transfer `Verifier` ownership to `ContractOwner` (if the deployer holds it at that point).

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

import {Test} from "forge-std/Test.sol";
import {ContractOwner} from "../contracts/ContractOwner.sol";
import {Verifier} from "../contracts/Verifier.sol";

contract PubKeyTest is Test {
    ContractOwner public contractOwner;
    Verifier public verifier;

    address multisig = makeAddr("multisig");
    address deployer = makeAddr("deployer");

    function setUp() public {
        vm.startPrank(deployer);

        // Verifier owner = deployer (msg.sender at initialize time)
        verifier = new Verifier();
        IVerifier.Point[8] memory emptyKeys;
        verifier.initialize(emptyKeys);

        // ContractOwner stores a reference to verifier but does NOT own it
        contractOwner = new ContractOwner();
        contractOwner.initialize(
            multisig, deployer,
            address(0), address(0), address(0), address(0),
            address(verifier), payable(address(0))
        );
        vm.stopPrank();

        // Confirm: ContractOwner is NOT the owner of Verifier
        assertEq(verifier.owner(), deployer);
        assertNotEq(verifier.owner(), address(contractOwner));
    }

    function test_AssignPubKeyAlwaysReverts() public {
        vm.prank(multisig);
        // Reverts: OwnableUnauthorizedAccount(address(contractOwner))
        vm.expectRevert();
        contractOwner.assignPubKey(0, 1, 2);
    }

    function test_DeletePubkeyAlwaysReverts() public {
        vm.prank(multisig);
        // Reverts: OwnableUnauthorizedAccount(address(contractOwner))
        vm.expectRevert();
        contractOwner.deletePubkey(0);
    }
}
```

### Citations

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

**File:** core/contracts/Verifier.sol (L41-48)
```text
    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```

**File:** core/contracts/Verifier.sol (L61-67)
```text
    function assignPubKey(
        uint256 i,
        uint256 x,
        uint256 y
    ) public onlyOwner {
        _assignPubkey(i, x, y);
    }
```

**File:** core/contracts/Verifier.sol (L85-91)
```text
    function deletePubkey(uint256 index) public onlyOwner {
        if (!isPointNone(pubkeys[index])) {
            nSigner -= 1;
            delete pubkeys[index];
        }
        emit DeletePubkey(index);
    }
```

**File:** core/contracts/Verifier.sol (L261-289)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
        bytes32 hashedMsg = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", data)
        );

        uint256 nSignatures = 0;
        for (uint256 i = 0; i < signatures.length; i++) {
            if (signatures[i].length > 0) {
                nSignatures += 1;
                require(
                    checkIndividualSignature(
                        hashedMsg,
                        signatures[i],
                        uint8(i)
                    ),
                    "invalid signature"
                );
            }
        }
        require(nSignatures == nSigner, "not enough signatures");
    }
```
