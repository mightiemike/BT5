### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain from Direct Deposit Accounts — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` directly without checking its return value and without using the project's own `safeTransferFrom` wrapper. Because the function has no access control beyond a chain-id check, any caller on chain 57073 (Ink) can trigger the execution path. If the USDC token returns `false` on a failed transfer instead of reverting, the function continues, drains usdcE from the target Direct Deposit Account (DDA), and sends it to the caller — with no USDC ever deposited.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public migration helper that is supposed to atomically swap usdcE held in a DDA for native USDC:

1. Pull USDC from `msg.sender` into the DDA via `transferFrom`.
2. Withdraw usdcE from the DDA to `ContractOwner` via `DirectDepositV1.withdraw`.
3. Forward the usdcE to `msg.sender` via `safeTransfer`.

The critical step is (1):

```solidity
// ContractOwner.sol line 616
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

This is a raw interface call whose `bool` return value is silently discarded. The project's own `ERC20Helper.safeTransferFrom` (which asserts the return value) is **not** used here, even though it is used everywhere else in the codebase for token pulls.

If `transferFrom` returns `false` (i.e., the caller has no allowance or insufficient balance and the token does not revert), execution falls through to steps (2) and (3), which unconditionally drain usdcE from the DDA and deliver it to the caller.

The function carries no `onlyOwner` or similar modifier — only a chain-id gate:

```solidity
// ContractOwner.sol line 608-609
function replaceUsdcEWithUsdc(bytes32 subaccount) external {
    require(block.chainid == 57073, ERR_UNAUTHORIZED);
```

Any address on Ink chain can call it for any subaccount that has a deployed DDA with a non-zero usdcE balance. [1](#0-0) 

For comparison, every other token pull in the codebase goes through the safe wrapper: [2](#0-1) [3](#0-2) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink returns `false` on a failed transfer (rather than reverting), an attacker can:

- Call `replaceUsdcEWithUsdc(victim_subaccount)` with zero USDC allowance.
- Receive the full usdcE balance of the victim's DDA at no cost.

The corrupted state delta is: usdcE balance of the DDA goes to zero; the attacker's wallet gains that usdcE; no USDC is deposited into the DDA. The victim's subaccount collateral is permanently reduced. [4](#0-3) 

---

### Likelihood Explanation

The exploitability depends on whether the hardcoded USDC token at that address returns `false` on failure rather than reverting. Standard Circle USDC reverts, which would prevent silent failure. However:

- The token address is on a newer chain (Ink / chain 57073) where the exact USDC implementation may differ from mainnet.
- The code pattern is unconditionally wrong regardless of current token behavior; a token upgrade or redeployment could silently activate the vulnerability.
- The entry path requires no privilege — any EOA on Ink can call the function.

Likelihood is **medium-low** given the token-behavior dependency, but the root cause is entirely within Nado code.

---

### Recommendation

Replace the raw `transferFrom` call with the project's existing `safeTransferFrom` wrapper from `ERC20Helper`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This mirrors the pattern used in `EndpointStorage.safeTransferFrom` and `ERC20Helper.safeTransferFrom`, which assert the boolean return value and revert on failure. [2](#0-1) 

---

### Proof of Concept

```
Precondition:
  - Chain ID = 57073 (Ink)
  - victim_subaccount has a deployed DDA (directDepositV1Address[victim_subaccount] != address(0))
  - DDA holds N usdcE tokens
  - USDC token returns false (not revert) on failed transferFrom

Attack:
  1. Attacker calls ContractOwner.replaceUsdcEWithUsdc(victim_subaccount)
     with zero USDC allowance granted to ContractOwner.

  2. Line 614: balance = usdcE.balanceOf(DDA) = N > 0  → enters if-block

  3. Line 616: usdc.transferFrom(attacker, DDA, N)
     → returns false, no USDC moved, no revert

  4. Line 617: DDA.withdraw(usdcE)
     → DDA.safeTransfer(usdcE, ContractOwner, N)
     → N usdcE now in ContractOwner

  5. Line 618: usdcE.safeTransfer(attacker, N)
     → N usdcE delivered to attacker

Result: Attacker receives N usdcE; DDA balance = 0; no USDC deposited.
        Victim subaccount collateral permanently drained.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/EndpointStorage.sol (L95-101)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
