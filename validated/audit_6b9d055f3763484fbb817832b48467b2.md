### Title
No Token Recovery Mechanism in `Airdrop` Contract Permanently Locks Surplus Tokens - (File: core/contracts/Airdrop.sol)

---

### Summary
`Airdrop.sol` holds ERC20 tokens for weekly Merkle-based distribution but exposes no `withdraw`, `rescue`, or `sweep` function. Any tokens sent to the contract in excess of the total Merkle-tree allocations — whether by owner over-funding or by any user sending tokens directly via ERC20 `transfer()` — are permanently and irrecoverably locked. Unlike the VestingTrustee analog (which at least allows a workaround via a new immediate-unlock grant), `Airdrop.sol` provides zero recovery path.

---

### Finding Description
`Airdrop.sol` is an upgradeable contract that receives ERC20 tokens and distributes them to users who submit valid Merkle proofs. The contract's full function surface is:

- `initialize` — sets `token` and `sanctions` addresses
- `registerMerkleRoot` (`onlyOwner`) — registers a weekly Merkle root
- `claim` / `_claim` / `_verifyProof` — user-facing claim flow
- `getClaimed` — view function [1](#0-0) 

There is no `withdraw`, `rescue`, `sweep`, or any other function that allows the owner or any party to recover tokens held by the contract. [2](#0-1) 

The `_claim` function transfers exactly `totalAmount` (the Merkle-committed amount) to the caller on each valid claim. The contract's token balance is therefore expected to equal the sum of all unclaimed Merkle allocations. Any balance above that sum is permanently stranded. [3](#0-2) 

This is confirmed by the interface `IAirdrop`, which also exposes no recovery entrypoint: [4](#0-3) 

Contrast this with `DirectDepositV1.sol`, which explicitly provides `withdraw()` and `withdrawNative()` for exactly this purpose: [5](#0-4) 

And `BaseWithdrawPool.sol`, which provides `removeLiquidity()` for the same reason: [6](#0-5) 

`Airdrop.sol` is the only token-holding contract in the protocol that omits this pattern entirely.

---

### Impact Explanation
Any tokens sent to `Airdrop.sol` beyond the sum of all Merkle-committed allocations are permanently locked with no on-chain recovery path. This includes:

1. **Owner over-funding**: The owner calls `registerMerkleRoot` for a week and then funds the contract. If the funded amount exceeds the actual sum of all leaf allocations in that week's tree (e.g., due to a calculation error or rounding), the surplus is locked forever.
2. **Accidental ERC20 transfer**: Any address — including a user, a bot, or another contract — can call `IERC20(token).transfer(airdropAddress, amount)` directly. Those tokens are immediately and permanently locked.

The corrupted state is: `IERC20(token).balanceOf(address(airdrop)) > sum_of_all_unclaimed_allocations`, with no mechanism to close the gap. [7](#0-6) 

---

### Likelihood Explanation
**Medium.** The owner must fund the contract each week to cover distributions. Any arithmetic discrepancy between the funded amount and the true sum of Merkle leaves (off-by-one, decimal mismatch, rounding) results in a permanent surplus. Additionally, the `token` address is public state, making accidental or deliberate direct ERC20 transfers trivially possible by any external party. Both paths require no special privilege beyond knowing the contract address. [8](#0-7) 

---

### Recommendation
Add an `onlyOwner` token recovery function to `Airdrop.sol`, analogous to the pattern already used in `DirectDepositV1.sol`:

```solidity
function withdrawSurplus(address to, uint256 amount) external onlyOwner {
    SafeERC20.safeTransfer(IERC20(token), to, amount);
}
```

This should be scoped to only allow withdrawal of tokens above the total outstanding (unclaimed) allocation to prevent the owner from draining legitimately owed user funds. [9](#0-8) 

---

### Proof of Concept

1. Owner deploys and initializes `Airdrop.sol` with `token = NADO`.
2. Owner calls `registerMerkleRoot(1, root)` where `root` commits to a total of `1_000_000e18` NADO across all leaves.
3. Owner sends `1_100_000e18` NADO to the contract (100k surplus due to miscalculation).
4. All users successfully claim their allocations; `1_000_000e18` NADO is distributed.
5. `IERC20(token).balanceOf(address(airdrop))` now equals `100_000e18`.
6. No function exists in `Airdrop.sol` to recover this balance. The `100_000e18` NADO is permanently locked. [3](#0-2) [10](#0-9)

### Citations

**File:** core/contracts/Airdrop.sol (L11-17)
```text
contract Airdrop is OwnableUpgradeable, IAirdrop {
    address internal token;
    address internal sanctions;
    uint32 internal pastWeeks;

    mapping(uint32 => bytes32) internal merkleRoots;
    mapping(uint32 => mapping(address => uint256)) internal claimed;
```

**File:** core/contracts/Airdrop.sol (L33-40)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }
```

**File:** core/contracts/Airdrop.sol (L65-83)
```text
    function _claim(
        uint32 week,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        _verifyProof(week, msg.sender, totalAmount, proof);
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
    }

    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }
```

**File:** core/contracts/interfaces/IAirdrop.sol (L1-19)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

interface IAirdrop {
    event Claim(address indexed account, uint32 week, uint256 amount);

    struct ClaimProof {
        uint32 week;
        uint256 totalAmount;
        bytes32[] proof;
    }

    function claim(ClaimProof[] calldata claimProofs) external;

    function getClaimed(address account)
        external
        view
        returns (uint256[] memory);
}
```

**File:** core/contracts/DirectDepositV1.sol (L103-112)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }

    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```
